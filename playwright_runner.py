"""
playwright_runner.py — Browser automation for live Yellow.ai bot testing.

Opens nexus.yellow.ai/liveBot/{bot_id} in a Playwright browser, interacts
with the chat widget, captures real bot responses, and streams events back
to the Flask SSE endpoint via a queue.

Design goals
────────────
1. FAST    — tight poll loop, pre-type next input during bot thinking, minimal
             sleeps. Single page load for the entire run, localStorage clear
             between tests (no new context spin-up per test).
2. STABLE  — DOM-order response capture: bot messages AFTER the last user
             bubble are the response. Welcome/greeting messages appear BEFORE
             the user bubble and are inherently excluded.
3. BUTTONS — click ANY visible button (quick reply, card button, widget button)
             when the test's user message matches its label.
4. SCOPED  — boundary_route tests are exempt from the single-agent guardrail;
             non-boundary tests that route out are flagged.
"""

import asyncio
import queue
import re

BOT_URL = "https://nexus.yellow.ai/liveBot/{bot_id}?region=&version=v3"

# ── Tunables ──────────────────────────────────────────────────────────────────

POLL_INTERVAL_SEC   = 0.08    # fast event-loop poll cadence
SETTLE_SEC          = 0.4     # stability window before declaring response done
RESPONSE_TIMEOUT    = 60      # hard cap on a single-turn wait
ROUTE_RE            = re.compile(r"\[ROUTE\s*TO:\s*([^\]]+)\]", re.IGNORECASE)

# ── Selector banks ────────────────────────────────────────────────────────────

INPUT_SELECTORS = [
    "input[placeholder='Ask your question']",
    "input[placeholder='Type your message']",
    "input[placeholder*='question' i]",
    "input[placeholder*='message' i]",
    "textarea[placeholder*='message' i]",
    "textarea[placeholder*='question' i]",
    "[class*='chat-input'] input",
    "[class*='input-box'] input",
    "[class*='ym-input'] input",
]

# V3 (current) widget uses [data-testid="message-*"]. Older widgets used the
# Tailwind class pattern below — we keep both, preferring testid.
YAI_BOT_MSG_SEL = "[data-testid='message-agent'], [class*='yai-group'][class*='yai-items-start']"

YAI_USER_MSG_SELECTORS = [
    "[data-testid='message-user']",
    "[class*='yai-group'][class*='yai-items-end']",
    "[class*='yai-items-end']",
    "[class*='user-message']",
    "[class*='sent-message']",
    "[class*='yai-justify-end']",
]

ALL_BUTTON_SELECTORS = [
    "button[class*='yai-rounded']",
    "button[class*='quick-reply']",
    "[class*='yai-quick'] button",
    "[role='button'][class*='yai-']",
    "button[class*='yai-']",
    "[class*='yai-items-start'] button",
    "[class*='yai-snap'] button",
    "[class*='yai-card'] button",
    "[class*='carousel'] button",
    "a[class*='yai-'][role='button']",
]

CAROUSEL_CARD_SELECTORS = [
    "[class*='yai-card']",
    "[class*='product-card']",
    "[class*='carousel'] [class*='item']",
    "[class*='carousel'] [class*='slide']",
    "[class*='swiper-slide']",
    "[class*='card-container']",
    "[class*='yai-snap'] > div",
    "[role='listitem'][class*='yai-']",
    "[class*='yai-items-start'] [class*='yai-rounded-2xl']",
]

SEND_SELECTORS = [
    "[class*='send-btn']",
    "[class*='sendBtn']",
    "[class*='send-button']",
    "button[class*='send']",
    "button[aria-label*='send' i]",
    "[class*='chat-footer'] button",
]

TRIGGER_SELECTORS = [
    ".ym-chat-icon",
    "#ymDivBar",
    "[class*='ym-chat-icon']",
    "[class*='ymChatIcon']",
    "[class*='chat-icon']",
    "[class*='bot-icon']",
    "[class*='chat-bubble']",
    "[class*='chat-trigger']",
    ".bot-icon",
]


# ── Frame helpers ─────────────────────────────────────────────────────────────

async def _get_ym_frame(page):
    for frame in page.frames:
        url = frame.url
        if "yellowmessenger.com" in url or "cdn.yellow.ai" in url:
            if "frame" in url or "widget" in url or "plugin" in url:
                return frame
    return None


async def _find_first(ctx, selectors: list, timeout_each: int = 3000):
    for sel in selectors:
        try:
            loc = ctx.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout_each)
            return loc
        except Exception:
            pass
    return None


# ── DOM readers ───────────────────────────────────────────────────────────────

# JS that returns EVERY message group in DOM order, each tagged user|bot.
# This is the single source of truth for message extraction. A group is
# classified as "user" via class names OR computed-style alignment so it
# works across all Yellow.ai V3 widget variants.
_JS_ALL_MESSAGES = """() => {
    // V3 widget: each message is tagged via [data-testid="message-agent"] or
    // [data-testid="message-user"]. These are stable across releases.
    // Older widgets used the yai-group class pattern — we keep it as fallback.
    const out = [];

    // ── Preferred: data-testid (V3 current) ──────────────────────────────────
    const v3 = document.querySelectorAll('[data-testid="message-agent"], [data-testid="message-user"]');
    if (v3.length) {
        for (const el of v3) {
            const role = el.dataset.testid === 'message-user' ? 'user' : 'bot';
            // Prefer the message-*-text child if present (excludes action buttons
            // like copy/like/dislike, which are siblings inside the bubble).
            const inner = el.querySelector('[data-testid="message-agent-text"], [data-testid="message-user-text"]')
                       || el;
            out.push({ role, text: (inner.innerText || '').trim() });
        }
        return out;
    }

    // ── Fallback: legacy yai-group class pattern ─────────────────────────────
    const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
    for (const g of groups) {
        const cls = g.className || '';
        let st = {};
        try { st = window.getComputedStyle(g); } catch (e) {}
        const isUser = cls.includes('items-end')
                    || cls.includes('justify-end')
                    || st.alignItems === 'flex-end'
                    || st.justifyContent === 'flex-end'
                    || st.alignSelf === 'flex-end'
                    || st.marginLeft === 'auto';
        out.push({ role: isUser ? 'user' : 'bot', text: (g.innerText || '').trim() });
    }
    return out;
}"""


async def _get_all_messages(page) -> list[dict]:
    """
    Return EVERY message group in DOM order as [{role, text}, ...].
    role is 'user' or 'bot'. This is the single source of truth.
    """
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []
    try:
        msgs = await ym_frame.evaluate(_JS_ALL_MESSAGES)
        return [m for m in (msgs or []) if isinstance(m, dict)]
    except Exception as e:
        print(f"[pw] _get_all_messages JS error: {e}")
        return []


async def _count_message_groups(page) -> int:
    """Total number of message groups (user + bot) currently in the DOM."""
    return len(await _get_all_messages(page))


def _norm(s: str) -> str:
    """Normalize text for resilient comparison (whitespace + case + punctuation-light)."""
    return " ".join((s or "").split()).strip().lower()


async def _snapshot_bot_texts(page) -> set[str]:
    """Snapshot all bot message texts currently in the DOM (normalized).
    Used to filter out welcome messages and replayed history from response capture.
    Stores both full text and a short prefix key for fuzzy matching."""
    msgs = await _get_all_messages(page)
    out: set[str] = set()
    for m in msgs:
        if m.get("role") != "bot":
            continue
        txt = (m.get("text") or "").strip()
        if len(txt) <= 1:
            continue
        normed = _norm(txt)
        out.add(normed)
        # Also store a short prefix for fuzzy matching — welcome messages
        # may get extra whitespace or trailing chars on re-render
        if len(normed) > 20:
            out.add(normed[:80])
    return out


_GREETING_PATTERNS = [
    "welcome", "how may i help", "how can i help", "how may i assist",
    "how can i assist", "what can i do for you", "assist you today",
    "help you today", "here to help", "here to assist",
]


def _is_greeting(text: str) -> bool:
    """Detect whether a bot message is a greeting/welcome message."""
    low = _norm(text)
    return any(p in low for p in _GREETING_PATTERNS)


def _strip_greeting_from_response(texts: list[str], is_first_turn: bool) -> list[str]:
    """
    No-op kept for call-site compatibility.

    Earlier versions stripped ANY greeting-looking text on the first turn, but
    that swallowed legitimate fallback responses (e.g. the bot replying
    "Hey there, welcome…" when the user's input is ambiguous and no agent
    matches). The pre_send_texts snapshot in _wait_for_response already
    handles dedup of the genuine welcome message that loads on page open,
    so this extra filter only ever produced false negatives.
    """
    return texts


async def _get_bot_messages_since(page, start_index: int) -> list[str]:
    """
    Bot message texts that appear at DOM index >= start_index, stopping at the
    NEXT user bubble. This ensures we only capture bot responses for the current
    turn, not messages from subsequent turns or replayed history.
    """
    msgs = await _get_all_messages(page)
    texts: list[str] = []
    found_user = False
    for idx in range(start_index, len(msgs)):
        m = msgs[idx]
        if m.get("role") == "user":
            if found_user:
                break
            found_user = True
            continue
        t = (m.get("text") or "").strip()
        if t and len(t) > 1:
            texts.append(t)
    return texts


async def _get_response_for_user(page, user_msg: str,
                                  min_user_index: int = 0) -> tuple[list[str], bool]:
    """
    Anchor the response to THIS turn's exact user bubble.

    Find the LAST user-role message group whose text matches `user_msg`
    (at DOM index >= min_user_index), then return every bot message that
    appears strictly AFTER it.

    Why this is bulletproof against server-side history replay:
    Yellow.ai keeps the conversation server-side, so after a page reload the
    entire old conversation can stream back into the DOM at unpredictable
    times. But ALL of that old history (old user bubbles + old bot bubbles)
    is positioned ABOVE the message the user just sent for this turn. By
    locating THIS turn's exact user bubble and reading only what comes after
    it, the welcome message, previous turns, and previous test cases are all
    excluded — regardless of when they render.

    Returns (bot_texts, anchored). `anchored` is False if the user bubble
    for this turn could not be located (caller should use index fallback).
    """
    msgs = await _get_all_messages(page)
    if not msgs:
        return [], False

    target = _norm(user_msg)
    anchor_idx = -1
    for idx in range(len(msgs)):
        if idx < min_user_index:
            continue
        m = msgs[idx]
        if m.get("role") != "user":
            continue
        utext = _norm(m.get("text") or "")
        if not utext:
            continue
        # Exact match first; fall back to containment (widget may trim/wrap)
        if utext == target or (target and (target in utext or utext in target)):
            anchor_idx = idx  # keep the LAST matching user bubble

    if anchor_idx == -1:
        return [], False

    texts: list[str] = []
    for idx in range(anchor_idx + 1, len(msgs)):
        m = msgs[idx]
        if m.get("role") == "user":
            # A later user bubble means we've passed this turn entirely.
            break
        t = (m.get("text") or "").strip()
        if t and len(t) > 1:
            texts.append(t)
    return texts, True


async def _get_user_bubble_count(page) -> int:
    msgs = await _get_all_messages(page)
    if msgs:
        return sum(1 for m in msgs if m.get("role") == "user")
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return 0
    best = 0
    for sel in YAI_USER_MSG_SELECTORS:
        try:
            els = await ym_frame.locator(sel).all()
            if len(els) > best:
                best = len(els)
        except Exception:
            pass
    return best


async def _get_bot_texts(page) -> list[str]:
    """All current bot message texts in order (includes welcome messages).
    Used as a FALLBACK when DOM-order approach can't find user bubbles."""
    ym_frame = await _get_ym_frame(page)

    if ym_frame:
        # Primary: yai-group items-start
        try:
            els = await ym_frame.locator(YAI_BOT_MSG_SEL).all()
            texts = []
            for el in els:
                try:
                    t = ""
                    try:
                        t = (await el.inner_text()).strip()
                    except Exception:
                        pass
                    if not t:
                        try:
                            t = (await el.text_content() or "").strip()
                            t = " ".join(t.split())
                        except Exception:
                            pass
                    if t:
                        texts.append(t)
                except Exception:
                    pass
            if texts:
                return texts
        except Exception:
            pass

        # Fallback: yai-group NOT items-end
        try:
            els = await ym_frame.locator("[class*='yai-group']").all()
            texts = []
            for el in els:
                try:
                    cls = await el.get_attribute("class") or ""
                    if "items-end" in cls:
                        continue
                    t = ""
                    try:
                        t = (await el.inner_text()).strip()
                    except Exception:
                        pass
                    if not t:
                        try:
                            t = (await el.text_content() or "").strip()
                            t = " ".join(t.split())
                        except Exception:
                            pass
                    if t:
                        texts.append(t)
                except Exception:
                    pass
            if texts:
                return texts
        except Exception:
            pass

    # Legacy selectors for older widget versions
    LEGACY = [
        ".ym-bot-message .ym-msg-txt", ".ym-bot-message",
        "[class*='bot-message'] [class*='text']", "[class*='bot-message']",
        "[class*='bot_message']", "[class*='received'] [class*='message-text']",
        "[class*='received']",
    ]
    ctx = ym_frame or page
    for sel in LEGACY:
        try:
            els = await ctx.locator(sel).all()
            if not els:
                continue
            texts = []
            for el in els:
                try:
                    t = (await el.inner_text()).strip()
                    if t:
                        texts.append(t)
                except Exception:
                    pass
            if texts:
                return texts
        except Exception:
            pass

    return []


async def _get_response_after_last_user(page) -> list[str]:
    """
    Extract bot message texts appearing AFTER the last user bubble in DOM order.
    Uses a comprehensive JS evaluation inside the iframe for maximum reliability
    across different widget variants.
    Returns [] if no user bubble can be found (caller should use fallback).
    """
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []

    _JS_GET_RESPONSE = """() => {
        // Gather ALL message groups
        const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
        if (!groups.length) return {found_user: false, texts: []};

        // Identify the LAST user message group using multiple heuristics
        let lastUserIdx = -1;
        for (let i = 0; i < groups.length; i++) {
            const cls = groups[i].className || '';
            const style = window.getComputedStyle(groups[i]);
            const isUser = cls.includes('items-end')
                        || cls.includes('justify-end')
                        || style.alignItems === 'flex-end'
                        || style.justifyContent === 'flex-end'
                        || style.alignSelf === 'flex-end'
                        || style.marginLeft === 'auto';
            if (isUser) lastUserIdx = i;
        }

        if (lastUserIdx === -1) return {found_user: false, texts: []};

        // Collect bot texts ONLY after the last user group
        const texts = [];
        for (let i = lastUserIdx + 1; i < groups.length; i++) {
            const cls = groups[i].className || '';
            const style = window.getComputedStyle(groups[i]);
            const isUser = cls.includes('items-end')
                        || cls.includes('justify-end')
                        || style.alignItems === 'flex-end'
                        || style.justifyContent === 'flex-end'
                        || style.alignSelf === 'flex-end'
                        || style.marginLeft === 'auto';
            if (isUser) continue;  // skip any user groups after (shouldn't happen)

            const t = (groups[i].innerText || '').trim();
            if (t && t.length > 1) texts.push(t);
        }
        return {found_user: true, texts: texts};
    }"""

    try:
        result = await ym_frame.evaluate(_JS_GET_RESPONSE)
        if result and result.get("found_user"):
            return result.get("texts", [])
    except Exception as e:
        print(f"[pw] _get_response_after_last_user JS error: {e}")

    # Playwright locator fallback
    try:
        groups = await ym_frame.locator("[class*='yai-group']").all()
    except Exception:
        return []

    if not groups:
        return []

    last_user_idx = -1
    for idx in range(len(groups)):
        try:
            cls = await groups[idx].get_attribute("class") or ""
            if "items-end" in cls or "justify-end" in cls:
                last_user_idx = idx
        except Exception:
            pass

    if last_user_idx == -1:
        return []

    texts: list[str] = []
    for idx in range(last_user_idx + 1, len(groups)):
        try:
            cls = await groups[idx].get_attribute("class") or ""
            if "items-end" in cls or "justify-end" in cls:
                continue
            t = ""
            try:
                t = (await groups[idx].inner_text()).strip()
            except Exception:
                pass
            if not t:
                try:
                    t = (await groups[idx].text_content() or "").strip()
                    t = " ".join(t.split())
                except Exception:
                    pass
            if t and len(t) > 1:
                texts.append(t)
        except Exception:
            pass

    return texts


async def _get_visible_buttons(page) -> list[tuple[str, object]]:
    """All visible button labels + their element references in the widget."""
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []
    buttons: list[tuple[str, object]] = []
    seen: set[str] = set()
    for sel in ALL_BUTTON_SELECTORS:
        try:
            els = await ym_frame.locator(sel).all()
            for el in els:
                try:
                    if not await el.is_visible():
                        continue
                    t = (await el.inner_text()).strip()
                    if t and len(t) < 100 and t.lower() not in {"send", "submit"} and t not in seen:
                        seen.add(t)
                        buttons.append((t, el))
                except Exception:
                    pass
        except Exception:
            pass
    return buttons


async def _get_button_labels(page) -> list[str]:
    return [label for label, _ in await _get_visible_buttons(page)]


async def _get_button_labels_after_user(page, user_msg: str) -> list[str]:
    """Get button labels that appear AFTER the current user bubble in the DOM.
    This ensures we only capture buttons belonging to the current turn's response,
    not stale buttons from previous turns."""
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []
    js = """(userMsg) => {
        const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
        const target = (userMsg || '').trim().toLowerCase();
        // Find the LAST user bubble matching userMsg
        let anchorIdx = -1;
        for (let i = 0; i < groups.length; i++) {
            const cls = groups[i].className || '';
            const st = window.getComputedStyle(groups[i]);
            const isUser = cls.includes('items-end') || cls.includes('justify-end')
                        || st.alignItems === 'flex-end' || st.justifyContent === 'flex-end'
                        || st.alignSelf === 'flex-end' || st.marginLeft === 'auto';
            if (!isUser) continue;
            const txt = (groups[i].innerText || '').trim().toLowerCase();
            if (target && (txt === target || txt.includes(target) || target.includes(txt))) {
                anchorIdx = i;
            }
        }
        if (anchorIdx === -1) return [];
        // Collect all buttons in bot groups AFTER the anchor
        const labels = [];
        const seen = new Set();
        for (let i = anchorIdx + 1; i < groups.length; i++) {
            const cls = groups[i].className || '';
            const st = window.getComputedStyle(groups[i]);
            const isUser = cls.includes('items-end') || cls.includes('justify-end')
                        || st.alignItems === 'flex-end' || st.justifyContent === 'flex-end'
                        || st.alignSelf === 'flex-end' || st.marginLeft === 'auto';
            if (isUser) break;  // Stop at next user bubble
            const btns = groups[i].querySelectorAll('button, [role="button"], a[class*="btn"], a[class*="button"]');
            for (const btn of btns) {
                const t = (btn.innerText || '').trim();
                if (t && t.length < 100 && t.length > 0
                    && !['send','submit'].includes(t.toLowerCase())
                    && !seen.has(t)) {
                    seen.add(t);
                    labels.push(t);
                }
            }
        }
        return labels;
    }"""
    try:
        result = await ym_frame.evaluate(js, user_msg)
        return result or []
    except Exception as e:
        print(f"[pw] _get_button_labels_after_user error: {e}")
        return []


def _is_bracketed_action(s: str) -> bool:
    """Check if a user message is a bracketed action instruction like [Shares live location]."""
    s = s.strip()
    return s.startswith("[") and s.endswith("]")


def _strip_brackets(s: str) -> str:
    """Strip surrounding brackets from test script actions like [Share My Location]."""
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    return s


_ACTION_NOISE_WORDS = {
    "clicks", "click", "taps", "tap", "shares", "share", "presses", "press",
    "selects", "select", "chooses", "choose", "hits", "hit", "submits", "submit",
    "the", "a", "an", "on", "via", "widget", "button", "but", "and", "then",
    "that", "which", "this", "my", "their", "its", "from", "with", "for",
}

_ACTION_STOP_WORDS = {"but", "and", "then", "however", "which", "where"}


def _extract_action_keywords(action_text: str) -> set[str]:
    """Extract meaningful keywords from a bracketed action description.
    E.g. 'Shares live location via widget, but location fails' → {'live', 'location'}"""
    text = action_text.lower()
    # Truncate at stop-clause words (e.g. "but location fails" is a condition, not the action)
    for stop in _ACTION_STOP_WORDS:
        parts = text.split(f" {stop} ")
        if len(parts) > 1:
            text = parts[0]
            break
    # Also truncate at commas (often separates action from condition)
    if "," in text:
        text = text.split(",")[0]
    words = set(re.split(r"[\s,;:.!?]+", text))
    return words - _ACTION_NOISE_WORDS - {""}


def _stem_simple(word: str) -> str:
    """Very basic stemming: remove common suffixes for matching."""
    w = word.lower()
    for suffix in ("ing", "tion", "es", "ed", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


def _fuzzy_button_match(target: str, btn_label: str, is_action: bool = False) -> bool:
    """Flexible button matching: exact, contains, or keyword overlap.
    When is_action=True, uses stemmed keyword matching for action descriptions."""
    t = target.lower().strip()
    b = btn_label.lower().strip()
    if not t or not b:
        return False
    if t == b:
        return True
    if t in b or b in t:
        return True

    t_words = set(t.split())
    b_words = set(b.split())
    common = t_words & b_words
    if len(common) >= 2 or (len(common) >= 1 and len(t_words) <= 2):
        return True

    if is_action:
        # For action descriptions, use keyword extraction + stemmed matching
        action_kw = _extract_action_keywords(target)
        btn_stems = {_stem_simple(w) for w in b_words} - _ACTION_NOISE_WORDS
        action_stems = {_stem_simple(w) for w in action_kw}
        stem_overlap = action_stems & btn_stems
        # Match if any meaningful stem overlaps (e.g. "locat" from both "location" and "location")
        if stem_overlap:
            return True
        # Also check if any action keyword is contained in any button word or vice versa
        for ak in action_kw:
            for bw in b_words - _ACTION_NOISE_WORDS:
                if len(ak) >= 4 and len(bw) >= 4 and (ak in bw or bw in ak):
                    return True

    return False


async def _click_button_by_label(page, label: str) -> bool:
    """Click a visible button matching label, but ONLY if it belongs to the
    LAST bot message group in the DOM (the most recent bot response).
    This prevents clicking stale buttons from earlier turns.
    Strips brackets from test script actions and uses fuzzy matching."""
    is_action = _is_bracketed_action(label)
    raw_target = _strip_brackets(label)

    # Get buttons scoped to the last bot message group only
    last_turn_buttons = await _get_last_turn_buttons(page)
    for btn_label, btn_el in last_turn_buttons:
        if _fuzzy_button_match(raw_target, btn_label, is_action=is_action):
            try:
                await btn_el.click()
                print(f"[pw] clicked button '{btn_label}' (matched '{raw_target}')")
                return True
            except Exception:
                pass
    return False


async def _get_last_turn_buttons(page) -> list[tuple[str, object]]:
    """Get buttons only from the LAST bot message group in the DOM.
    This ensures we never click stale buttons from previous turns."""
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []

    # Find the last bot message group in the DOM
    try:
        # Get all yai-groups, find the last bot one
        last_bot_idx = await ym_frame.evaluate("""() => {
            const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
            let lastBotIdx = -1;
            for (let i = groups.length - 1; i >= 0; i--) {
                const cls = groups[i].className || '';
                const st = window.getComputedStyle(groups[i]);
                const isUser = cls.includes('items-end') || cls.includes('justify-end')
                            || st.alignItems === 'flex-end' || st.justifyContent === 'flex-end'
                            || st.alignSelf === 'flex-end' || st.marginLeft === 'auto';
                if (!isUser) { lastBotIdx = i; break; }
            }
            if (lastBotIdx >= 0) {
                groups[lastBotIdx].setAttribute('data-yk-last-bot-group', '1');
            }
            return lastBotIdx;
        }""")
    except Exception:
        return []

    if last_bot_idx < 0:
        return []

    buttons: list[tuple[str, object]] = []
    seen: set[str] = set()
    for sel in ALL_BUTTON_SELECTORS:
        try:
            els = await ym_frame.locator(
                f"[data-yk-last-bot-group='1'] {sel}"
            ).all()
            for el in els:
                try:
                    if not await el.is_visible():
                        continue
                    t = (await el.inner_text()).strip()
                    if t and len(t) < 100 and t.lower() not in {"send", "submit"} and t not in seen:
                        seen.add(t)
                        buttons.append((t, el))
                except Exception:
                    pass
        except Exception:
            pass

    # Clean up the tag
    try:
        await ym_frame.evaluate(
            "() => { const el = document.querySelector('[data-yk-last-bot-group]'); "
            "if (el) el.removeAttribute('data-yk-last-bot-group'); }"
        )
    except Exception:
        pass

    return buttons


# ── Carousel / widget capture ────────────────────────────────────────────────
#
# Yellow.ai V3 widget structure (verified live):
#
#   data-testid="message-agent"          — a bot message bubble (text OR widget)
#     └── data-testid="message-agent-widget"   — a rich card (image + title +
#                                                 subtitle + N buttons)
#
#   Multi-card carousels are paginated, not stacked — only the CURRENTLY VISIBLE
#   card is in the DOM. Navigation arrows (aria-label="Previous card" /
#   "Next card") cycle through cards.
#
# Capture strategy:
#   1. Find all message-agent-widget elements inside the LAST bot message group.
#   2. For each widget, extract structured fields (title, subtitle, button
#      labels) — NOT the raw innerText, which would duplicate everything.
#   3. If "Next card" arrow exists and is enabled, click it, wait for the new
#      card to render, capture it, repeat until the arrow disables or stops
#      progressing. Cap at 8 cards as a safety limit.

# CSS selector for the next-card navigation arrow.
# Covers all known aria-label and class-name variants across Yellow.ai widget versions.
_NEXT_CARD_SELECTOR = (
    "button[aria-label='Next card'], button[aria-label='next card' i], "
    "button[aria-label='Next'], button[aria-label='next' i], "
    "button[aria-label='Next slide'], button[aria-label='next slide' i], "
    "button[aria-label='next item' i], button[aria-label='forward' i], "
    "[class*='next-card'], [class*='nextCard'], [class*='next-slide'], "
    "[class*='carousel-next'], [class*='slick-next'], [class*='swiper-next'], "
    "[data-testid*='next-card'], [data-testid*='nextCard']"
)

# JS fallback: find the next-arrow ONLY within the tagged carousel message.
# Intentionally conservative — only matches buttons with "next" in aria/class
# OR small icon-only buttons (arrow icons, not readable quick-reply labels).
# Never falls back to "rightmost button" to avoid clicking quick replies.
_JS_FIND_NEXT_ARROW = """() => {
    const msg = document.querySelector('[data-yk-last-widget-msg]');
    if (!msg) return null;
    const btns = Array.from(msg.querySelectorAll('button'));
    // 1. aria-label contains "next"
    const byAria = btns.find(b => /next/i.test(b.getAttribute('aria-label') || ''));
    if (byAria) return byAria;
    // 2. class name contains "next"
    const byCls = btns.find(b => /next/i.test(b.className || ''));
    if (byCls) return byCls;
    // 3. Small icon-only button (no readable text, likely SVG arrow) on the right side
    const iconBtns = btns.filter(b => {
        const r = b.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const text = (b.innerText || '').trim();
        // Must be a small button (< 56px) with no readable label or only an arrow char
        return r.width < 56 && (text.length === 0 || /^[>›→❯]$/.test(text));
    });
    if (iconBtns.length > 0) {
        // Pick the rightmost one — that's the forward/next arrow
        iconBtns.sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
        return iconBtns[0];
    }
    return null;
}"""

# JS to tag the correct bot message for the current turn that contains a
# valid carousel widget (heading/image required — pure-button widgets are
# quick-reply containers, not carousel cards).
# Parameters: userMsg (str), anchorSelector (str for the user-group class)
_JS_TAG_CAROUSEL_MSG = """(userMsg) => {
    // Step 1: find the last user bubble that contains userMsg.
    // Uses the SAME robust user-detection as _JS_ALL_MESSAGES (class + computed style).
    let anchorEl = null;
    if (userMsg && userMsg.trim()) {
        const snippet = userMsg.trim().slice(0, 60).toLowerCase();
        const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
        for (let i = groups.length - 1; i >= 0; i--) {
            const g = groups[i];
            const cls = g.className || '';
            let st = {};
            try { st = window.getComputedStyle(g); } catch(e) {}
            const isUser = cls.includes('items-end')
                        || cls.includes('justify-end')
                        || st.alignItems === 'flex-end'
                        || st.justifyContent === 'flex-end'
                        || st.alignSelf === 'flex-end'
                        || st.marginLeft === 'auto';
            if (isUser && (g.innerText || '').toLowerCase().includes(snippet)) {
                anchorEl = g;
                break;
            }
        }
    }

    // CRITICAL: if we can't find the user bubble, return false.
    // Never search without an anchor — that picks up stale widgets from old turns.
    if (!anchorEl) return false;

    // Step 2: walk all message-agent elements in reverse (newest first).
    // Find the last one that: (a) appears AFTER the anchor, and
    // (b) contains a widget with a heading or image (real carousel card,
    //     not a quick-reply container).
    const msgs = Array.from(document.querySelectorAll('[data-testid="message-agent"]'));
    for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i];

        // Enforce anchor: skip messages that precede the user bubble.
        const pos = anchorEl.compareDocumentPosition(msg);
        // Node.DOCUMENT_POSITION_FOLLOWING = 4  (msg comes after anchor)
        if (!(pos & 4)) continue;

        const widget = msg.querySelector('[data-testid="message-agent-widget"]');
        if (!widget) continue;

        // Validate: a real carousel card must have a visible heading or image.
        // A widget that contains ONLY buttons is a quick-reply container.
        const hasHeading = widget.querySelector(
            'h1,h2,h3,h4,h5,h6,[role="heading"]'
        );
        const hasImage = widget.querySelector('img');
        const hasBoldText = Array.from(widget.querySelectorAll('div,span,p')).some(e =>
            e.children.length === 0 &&
            (e.textContent || '').trim() &&
            parseInt(window.getComputedStyle(e).fontWeight || '400') >= 600
        );

        if (hasHeading || hasImage || hasBoldText) {
            msg.setAttribute('data-yk-last-widget-msg', '1');
            return true;
        }
    }
    return false;
}"""


async def _extract_widget_struct(widget_el) -> dict:
    """Pull structured fields from a single message-agent-widget."""
    return await widget_el.evaluate(r"""(w) => {
        function collapseWS(s) { return (s || '').trim().replace(/\s+/g, ' '); }
        const imgs = Array.from(w.querySelectorAll('img'));
        const imgAlt = imgs.map(i => i.alt || '').filter(Boolean)[0] || '';

        // Title: prefer first heading; fallback to first leaf div/span/p with bold-ish weight.
        let title = '';
        const h = w.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
        if (h) title = collapseWS(h.textContent);
        if (!title) {
            const leaves = Array.from(w.querySelectorAll('div,span,p')).filter(e =>
                e.children.length === 0 && (e.textContent || '').trim());
            for (const el of leaves) {
                const w_ = parseInt(window.getComputedStyle(el).fontWeight || '400');
                if (w_ >= 600) { title = collapseWS(el.textContent); break; }
            }
            if (!title && leaves.length) title = collapseWS(leaves[0].textContent);
        }

        // Buttons inside this widget
        const btns = Array.from(w.querySelectorAll('button'))
            .map(b => collapseWS(b.innerText || b.textContent || ''))
            .filter(t => t && t.length > 0 && t.length < 120);

        // Subtitle / description: any leaf text that is NOT the title and NOT a button label
        const btnSet = new Set(btns.map(b => b.toLowerCase()));
        const leafTexts = Array.from(w.querySelectorAll('div,span,p'))
            .filter(e => e.children.length === 0)
            .map(e => collapseWS(e.textContent))
            .filter(t => t && t.toLowerCase() !== title.toLowerCase() && !btnSet.has(t.toLowerCase()));
        const subtitle = leafTexts.length ? leafTexts[0] : '';

        return {
            title: title || (imgAlt || ''),
            subtitle: (subtitle && subtitle !== title) ? subtitle : '',
            image_alt: imgAlt,
            buttons: btns,
        };
    }""")


def _format_card(card: dict) -> str:
    """Render one structured card as a single readable line."""
    head = card.get("title", "").strip() or card.get("image_alt", "").strip() or "(card)"
    sub  = card.get("subtitle", "").strip()
    btns = [b for b in card.get("buttons", []) if b]
    parts = [head]
    if sub: parts.append(f"— {sub}")
    line = " ".join(parts)
    if btns:
        line += "\n     buttons: " + " | ".join(btns)
    return line


async def _capture_carousel(page, user_msg: str = "", max_cards: int = 8) -> list[dict]:
    """
    Capture the current bot turn's carousel as a list of structured cards.

    Key guarantees:
    - Anchored to the current user turn: only considers bot messages that appear
      AFTER the current user bubble in the DOM, so stale carousels from earlier
      turns are never mistaken for the current response.
    - Card validation: a widget element is only treated as a carousel card if it
      contains a heading, image, or bold text.  Pure-button widgets (quick-reply
      containers) are rejected and left to _get_button_labels.
    - Safe arrow navigation: the next-card JS fallback only clicks small icon-only
      buttons; it never clicks readable quick-reply labels.
    """
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []

    # Always clean up any tag left by a previous run first.
    try:
        await ym_frame.evaluate(
            "() => { const el = document.querySelector('[data-yk-last-widget-msg]'); "
            "if (el) el.removeAttribute('data-yk-last-widget-msg'); }"
        )
    except Exception:
        pass

    # Locate the CURRENT TURN's bot message that contains a valid carousel card.
    # _JS_TAG_CAROUSEL_MSG anchors to the current user bubble and validates that
    # the widget has heading/image content (not just buttons).
    try:
        found = await ym_frame.evaluate(_JS_TAG_CAROUSEL_MSG, user_msg)
    except Exception:
        found = False
    if not found:
        return []

    cards: list[dict] = []
    seen_signatures: set[str] = set()

    for _ in range(max_cards):
        # Locate the visible widget inside the tagged message.
        try:
            widget = ym_frame.locator(
                "[data-yk-last-widget-msg='1'] [data-testid='message-agent-widget']"
            ).first
            if not await widget.is_visible(timeout=600):
                break
        except Exception:
            break

        try:
            card = await _extract_widget_struct(widget)
        except Exception as e:
            print(f"[pw] widget extract error: {e}")
            break

        # Skip cards that have no title/image (pure-button widget = quick replies)
        if not (card.get("title") or card.get("image_alt")):
            print("[pw] widget has no title/image — treating as quick-reply container, skipping")
            break

        sig = (card.get("title", "") + "|" + "|".join(card.get("buttons", []))).strip()
        if not sig or sig in seen_signatures:
            break   # Carousel did not advance — we've seen all cards
        seen_signatures.add(sig)
        cards.append(card)
        print(f"[pw] carousel card {len(cards)}: {card.get('title', '(no title)')}")

        # Advance to the next card.
        # Strategy 1: CSS selector (aria-label / class variants)
        # Strategy 2: JS fallback (icon-only small buttons only — never quick-reply labels)
        clicked = False
        try:
            next_btn = ym_frame.locator(_NEXT_CARD_SELECTOR).first
            if await next_btn.is_visible(timeout=400):
                disabled     = await next_btn.get_attribute("disabled")
                aria_disabled = await next_btn.get_attribute("aria-disabled")
                if disabled is None and not (aria_disabled and aria_disabled.lower() == "true"):
                    await next_btn.click(timeout=2000)
                    clicked = True
        except Exception:
            pass

        if not clicked:
            try:
                el = await ym_frame.evaluate_handle(_JS_FIND_NEXT_ARROW)
                if el:
                    obj = el.as_element()
                    if obj and await obj.is_visible():
                        await obj.click(timeout=2000)
                        clicked = True
            except Exception:
                pass

        if not clicked:
            break   # No navigation arrow found — single card or last card
        await asyncio.sleep(0.7)   # Wait for the next card to render

    # Cleanup tag
    try:
        await ym_frame.evaluate(
            "() => { const el = document.querySelector('[data-yk-last-widget-msg]'); "
            "if (el) el.removeAttribute('data-yk-last-widget-msg'); }"
        )
    except Exception:
        pass

    if cards:
        print(f"[pw] carousel: captured {len(cards)} card(s)")
    return cards


# Kept for backward compatibility with older callers; returns the structured
# cards rendered as text lines. New code should call _capture_carousel directly.
async def _get_carousel_texts(page, user_msg: str = "") -> list[str]:
    cards = await _capture_carousel(page, user_msg=user_msg)
    return [_format_card(c) for c in cards]


async def _is_bot_thinking(page) -> bool:
    """Fast JS-based thinking indicator detection."""
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return False
    try:
        return await ym_frame.evaluate(
            "document.body?.innerText?.includes('Thinking') || false"
        )
    except Exception:
        return False


# ── Wait primitives ───────────────────────────────────────────────────────────

async def _wait_for_widget_idle(page, max_wait_sec: float = 12.0,
                                 stable_window: float = 1.0) -> int:
    """
    Wait until the widget is fully settled BEFORE the first message of a test:
    the message-group count must be stable for `stable_window` seconds and the
    bot must not be 'Thinking'. This guarantees the welcome message has fully
    rendered and is counted, so it lands BELOW the start_index and is excluded
    from every captured response.

    Returns the final stable message-group count.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait_sec
    last_count = -1
    stable_since = loop.time()

    while loop.time() < deadline:
        count = await _count_message_groups(page)
        thinking = await _is_bot_thinking(page)
        now = loop.time()

        if count != last_count or thinking:
            last_count = count
            stable_since = now
        elif (now - stable_since) >= stable_window:
            return count

        await asyncio.sleep(POLL_INTERVAL_SEC)

    return await _count_message_groups(page)


async def _wait_for_response(page, user_msg: str, start_index: int,
                              settle_sec: float = SETTLE_SEC,
                              timeout_sec: float = RESPONSE_TIMEOUT,
                              on_thinking_start=None,
                              pre_send_texts: set[str] | None = None,
                              is_button_click: bool = False,
                              is_first_turn: bool = False) -> list[str]:
    """
    Wait until the bot finishes responding, then return ONLY the bot messages
    for the CURRENT turn.

    PRIMARY anchor: locate THIS turn's exact user bubble (text == user_msg)
    and read only the bot messages strictly after it. Immune to server-side
    history that streams back into the DOM after a reload, because all of that
    old content is positioned ABOVE this turn's user bubble.

    FALLBACK: if the user bubble can't be matched (unusual widget variant or
    button-click with no echoed text), fall back to start_index scoping.

    For button clicks (is_button_click=True): no user bubble will exist, so we
    rely entirely on the pre_send_texts filter to identify NEW bot messages
    that appeared after the click.

    pre_send_texts: normalized bot texts present in the DOM before the user
    message was sent. Any captured text matching this set is filtered out
    (handles welcome messages and replayed history that render after the user
    bubble).

    Calls on_thinking_start() once when thinking is detected (for pre-typing).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    last_sig = ""
    stable_since = loop.time()
    thinking_callback_fired = False
    _pre = pre_send_texts or set()

    def _filter(texts: list[str]) -> list[str]:
        # The only legitimate filter is "drop bot messages that already existed
        # in the DOM BEFORE we sent the user message" (welcome message, replayed
        # history). We intentionally do NOT strip greeting-shaped text on the
        # first turn — if the bot's actual response uses "welcome"/"how can I
        # help" wording (e.g. ambiguous-intent fallback), that IS the response
        # and must be returned to the caller.
        if not _pre:
            return texts
        def _in_pre(t):
            nt = _norm(t)
            if nt in _pre:
                return True
            # Fuzzy: substring containment, prefix match, or high word overlap
            for p in _pre:
                if len(p) < 10:
                    continue
                # Exact substring either direction
                if p in nt or nt in p:
                    return True
                # Prefix match (first 60 chars) — catches re-rendered text
                if nt[:60] == p[:60]:
                    return True
                # Word overlap — if 80%+ words match, it's the same message
                nt_words = set(nt.split())
                p_words = set(p.split())
                if nt_words and p_words:
                    overlap = len(nt_words & p_words)
                    if overlap / max(1, min(len(nt_words), len(p_words))) > 0.8:
                        return True
            return False
        filtered = [t for t in texts if not _in_pre(t)]
        if len(filtered) < len(texts):
            dropped = len(texts) - len(filtered)
            print(f"[pw] _filter: dropped {dropped} pre-existing text(s)")
        return filtered

    async def _capture() -> list[str]:
        if not is_button_click:
            anchored_texts, anchored = await _get_response_for_user(
                page, user_msg, min_user_index=start_index
            )
            if anchored:
                return _filter(anchored_texts)

        # For button clicks or when anchor fails: get ALL bot messages currently
        # in the DOM and filter against what existed before the click/send.
        all_msgs = await _get_all_messages(page)
        all_bot_texts = [
            (m.get("text") or "").strip() for m in all_msgs
            if m.get("role") == "bot" and len((m.get("text") or "").strip()) > 1
        ]
        return _filter(all_bot_texts)

    while loop.time() < deadline:
        new_texts = await _capture()
        sig = "||".join(f"{len(t)}:{t[:60]}" for t in new_texts)
        thinking = await _is_bot_thinking(page)
        now = loop.time()

        if thinking and not thinking_callback_fired and on_thinking_start is not None:
            thinking_callback_fired = True
            try:
                await on_thinking_start()
            except Exception:
                pass

        if sig != last_sig or thinking:
            last_sig = sig
            stable_since = now
        else:
            if new_texts and (now - stable_since) >= settle_sec:
                if not thinking_callback_fired and on_thinking_start is not None:
                    thinking_callback_fired = True
                    try:
                        await on_thinking_start()
                    except Exception:
                        pass
                return new_texts

        await asyncio.sleep(POLL_INTERVAL_SEC)

    # Timeout — return best effort
    final = await _capture()
    if final:
        print(f"[pw] _wait_for_response: TIMEOUT — returning {len(final)} "
              f"best-effort texts")
        return final
    print(f"[pw] _wait_for_response: TIMEOUT — no new texts captured")
    return []


# ── Widget interaction ────────────────────────────────────────────────────────

async def _open_widget(page, max_wait_sec: float = 12.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max_wait_sec
    trigger_clicked = False

    while loop.time() < deadline:
        for sel in INPUT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=200):
                    return True
            except Exception:
                pass

        ym_frame = await _get_ym_frame(page)
        if ym_frame:
            for sel in INPUT_SELECTORS:
                try:
                    loc = ym_frame.locator(sel).first
                    if await loc.is_visible(timeout=200):
                        return True
                except Exception:
                    pass

        if not trigger_clicked:
            trigger = await _find_first(page, TRIGGER_SELECTORS, timeout_each=200)
            if trigger:
                try:
                    await trigger.click()
                    trigger_clicked = True
                    await asyncio.sleep(0.5)
                    continue
                except Exception:
                    pass

        await asyncio.sleep(0.15)

    return False


async def _resolve_input(page):
    ym_frame = await _get_ym_frame(page)
    contexts = [page]
    if ym_frame:
        contexts.append(ym_frame)
    timeouts = [150, 3000]
    for ctx, timeout in zip(contexts, timeouts):
        inp = await _find_first(ctx, INPUT_SELECTORS, timeout_each=timeout)
        if inp:
            return inp, ctx
    return None, None


async def _pretype_input(page, text: str) -> bool:
    if not text or not text.strip() or "{{OTP}}" in text:
        return False
    inp, _ = await _resolve_input(page)
    if not inp:
        return False
    try:
        await inp.click()
        await inp.fill(text)
        return True
    except Exception:
        return False


async def _send_message(page, text: str) -> bool:
    inp, ctx = await _resolve_input(page)
    if not inp:
        print("[pw] _send_message: no input found")
        return False

    target = text.strip()
    try:
        try:
            current = (await inp.input_value() or "").strip()
        except Exception:
            current = ""

        if current != target:
            try:
                await inp.click()
            except Exception:
                pass
            await inp.fill(text)

        try:
            await inp.focus()
        except Exception:
            try:
                await inp.click()
            except Exception:
                pass

        await inp.press("Enter")
        await asyncio.sleep(0.08)

        try:
            post_val = (await inp.input_value() or "").strip()
        except Exception:
            post_val = ""
        if post_val:
            send = await _find_first(ctx, SEND_SELECTORS, timeout_each=800)
            if send:
                try:
                    await send.click()
                except Exception:
                    pass
        return True
    except Exception as e:
        print(f"[pw] _send_message error: {e}")
        return False


async def _verify_user_bubble_rendered(page, prev_user_count: int,
                                        timeout_sec: float = 2.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        if await _get_user_bubble_count(page) > prev_user_count:
            return True
        await asyncio.sleep(POLL_INTERVAL_SEC)
    return False


# ── Session reset (no new context — reuse same page) ─────────────────────────

async def _clear_frame_storage(page):
    """Clear localStorage/sessionStorage/IndexedDB inside the Yellow.ai widget
    iframe. This is critical because the iframe has a DIFFERENT origin
    (cdn.yellow.ai / yellowmessenger.com) than the parent page, so clearing
    the parent's storage does NOT affect the iframe's conversation/user ID."""
    ym_frame = await _get_ym_frame(page)
    if ym_frame:
        try:
            await ym_frame.evaluate("""async () => {
                try { localStorage.clear(); } catch(e) {}
                try { sessionStorage.clear(); } catch(e) {}
                try {
                    if (window.indexedDB && indexedDB.databases) {
                        const dbs = await indexedDB.databases();
                        for (const d of dbs) {
                            if (d && d.name) { try { indexedDB.deleteDatabase(d.name); } catch(e) {} }
                        }
                    }
                } catch(e) {}
                try {
                    document.cookie.split(';').forEach(c => {
                        const n = c.split('=')[0].trim();
                        document.cookie = n + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
                    });
                } catch(e) {}
            }""")
        except Exception:
            pass


async def _reset_for_next_test(page, bot_url: str):
    """
    Reset the conversation for the next test. Yellow.ai keys the conversation
    to a device/user id stored in the WIDGET IFRAME's localStorage/IndexedDB
    (different origin from the parent page). We must clear storage in BOTH the
    parent page AND the iframe, then reload to mint a fresh anonymous user.
    """
    # Clear the widget iframe's storage FIRST (before it's destroyed by reload)
    await _clear_frame_storage(page)

    # Clear the parent page's storage
    try:
        await page.evaluate("""async () => {
            try { localStorage.clear(); } catch(e) {}
            try { sessionStorage.clear(); } catch(e) {}
            try {
                if (window.indexedDB && indexedDB.databases) {
                    const dbs = await indexedDB.databases();
                    for (const d of dbs) {
                        if (d && d.name) { try { indexedDB.deleteDatabase(d.name); } catch(e) {} }
                    }
                }
            } catch(e) {}
            try {
                document.cookie.split(';').forEach(c => {
                    const n = c.split('=')[0].trim();
                    document.cookie = n + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
                });
            } catch(e) {}
        }""")
    except Exception:
        pass

    # Clear cookies at the browser-context level (covers HttpOnly cookies
    # and cookies from ALL origins including the iframe's).
    try:
        await page.context.clear_cookies()
    except Exception:
        pass

    # Clear storage permissions/state for the iframe origins
    try:
        await page.context.clear_permissions()
    except Exception:
        pass

    try:
        await page.reload(wait_until="load", timeout=30_000)
    except Exception:
        try:
            await page.goto(bot_url, wait_until="load", timeout=45_000)
        except Exception as e:
            raise RuntimeError(f"Page reload failed: {e}")

    for _ in range(30):
        if await _get_ym_frame(page):
            break
        await asyncio.sleep(0.2)

    # Clear the iframe's storage AGAIN after it reloads (in case the iframe
    # re-initialized with a cached user ID from a service worker or other source)
    await asyncio.sleep(0.5)
    await _clear_frame_storage(page)

    await asyncio.sleep(0.3)
    if not await _open_widget(page, max_wait_sec=10.0):
        raise RuntimeError("Widget did not open after reset")

    # Wait for any replayed history / welcome messages to fully render so
    # the pre-send snapshot in _run_one_test captures them for filtering.
    await _wait_for_widget_idle(page, max_wait_sec=10.0, stable_window=2.0)


# ── Initial page load ────────────────────────────────────────────────────────

async def _prepare_page_for_test(page, bot_url: str, max_attempts: int = 3):
    last_err: str = "unknown"
    for attempt in range(1, max_attempts + 1):
        try:
            # Use "domcontentloaded" instead of "load" — the Yellow.ai liveBot
            # page keeps the load event pending on slow third-party scripts
            # (analytics, etc.) and will hit the 45s timeout on a normal
            # network. We don't need the load event — we only care that the
            # HTML is parsed; the widget iframe is awaited separately below.
            await page.goto(bot_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            # On timeout, fall back to "commit" (HTML headers received) so we
            # can still try to find the widget on a slow/flaky network.
            print(f"[pw] _prepare_page (attempt {attempt}): goto domcontentloaded failed: {e}")
            try:
                await page.goto(bot_url, wait_until="commit", timeout=20_000)
            except Exception as e2:
                last_err = f"navigation failed: {e2}"
                print(f"[pw] _prepare_page (attempt {attempt}): goto commit also failed: {e2}")
                await asyncio.sleep(1.0)
                continue

        iframe_seen = False
        # Up to ~20s for the widget iframe — covers slow first paint after
        # we drop the load-event wait above.
        for _ in range(66):
            if await _get_ym_frame(page):
                iframe_seen = True
                break
            await asyncio.sleep(0.3)

        if not iframe_seen:
            last_err = "widget iframe never loaded"
            print(f"[pw] _prepare_page (attempt {attempt}): {last_err}")
            await asyncio.sleep(1.0)
            continue

        # Force the widget iframe visible — nexus.yellow.ai/liveBot sets it
        # display:none by default; without this the input is never found.
        await page.evaluate("""() => {
            const iframe = document.querySelector('#ym-widget-v3-frame');
            if (iframe) {
                iframe.style.cssText = 'display:block !important; position:fixed; bottom:0; right:0; width:420px; height:700px; z-index:9999; border:none;';
            }
        }""")

        await asyncio.sleep(0.3)
        if await _open_widget(page, max_wait_sec=15.0):
            return

        last_err = "widget loaded but input never became visible"
        print(f"[pw] _prepare_page (attempt {attempt}): {last_err}")
        await asyncio.sleep(1.0)

    raise RuntimeError(
        f"Could not open the chat widget after {max_attempts} attempts. "
        f"Last error: {last_err}"
    )


# ── Per-test runner ───────────────────────────────────────────────────────────

async def _run_one_test(page, test: dict, eq: queue.Queue,
                        otp_queue: queue.Queue | None,
                        single_agent_name: str | None) -> tuple[list[dict], str | None]:
    turns_out: list[dict] = []
    scope_violation: str | None = None
    turns = test.get("turns", [])

    # Wait until the widget is fully idle so the welcome message and any
    # replayed history have rendered.
    await asyncio.sleep(0.5)
    await _wait_for_widget_idle(page, max_wait_sec=15.0, stable_window=2.0)
    # Extra wait + re-check to guarantee the welcome message is fully in DOM
    await asyncio.sleep(1.5)
    await _wait_for_widget_idle(page, max_wait_sec=5.0, stable_window=1.5)
    prev_user_bubble = await _get_user_bubble_count(page)

    # ── Capture the welcome/initial bot text as a permanent exclusion set ──
    # This is taken ONCE before any user message is sent. Every bot message
    # currently in the DOM is a welcome/greeting/replayed message and must
    # NEVER appear as a response to any turn.
    welcome_texts = await _snapshot_bot_texts(page)
    print(f"[pw] welcome exclusion set: {len(welcome_texts)} texts captured")

    for i, turn in enumerate(turns):
        user_msg = turn.get("user", "")
        expected = turn.get("expected", "")

        # ── OTP placeholder ─────────────────────────────────────────────────
        if "{{OTP}}" in user_msg and otp_queue is not None:
            eq.put({"type": "pw_otp_needed", "test_id": test["test_id"],
                    "turn": i + 1})
            print(f"[pw] turn {i+1}: OTP needed — waiting")
            try:
                otp_value = await asyncio.wait_for(
                    asyncio.to_thread(otp_queue.get, timeout=300), timeout=310
                )
            except (asyncio.TimeoutError, Exception):
                otp_value = ""
            if otp_value:
                user_msg = user_msg.replace("{{OTP}}", otp_value)
                eq.put({"type": "pw_otp_received", "test_id": test["test_id"],
                        "turn": i + 1})
            else:
                actual = "(ERROR: OTP not provided within timeout)"
                eq.put({"type": "pw_turn_bot", "test_id": test["test_id"],
                        "turn": i + 1, "message": actual})
                turns_out.append({"turn": i + 1, "user": user_msg,
                                   "expected": expected, "actual": actual})
                break

        eq.put({"type": "pw_turn_user", "test_id": test["test_id"],
                "turn": i + 1, "message": user_msg})

        # ── Record message-group count and snapshot bot texts BEFORE sending ──
        # The snapshot captures all bot texts currently in the DOM so they can
        # be filtered from the response. Always include the welcome exclusion
        # set so it's impossible for the welcome message to leak through.
        start_index = await _count_message_groups(page)
        pre_send_texts = await _snapshot_bot_texts(page)
        pre_send_texts = pre_send_texts | welcome_texts   # always exclude welcome
        print(f"[pw] turn {i+1}: start_index={start_index}, "
              f"pre_send snapshot has {len(pre_send_texts)} bot texts")

        # ── Send: try clicking ANY matching button first ────────────────────
        clicked_button = await _click_button_by_label(page, user_msg)
        if clicked_button:
            print(f"[pw] turn {i+1}: clicked button '{user_msg}'")

        if not clicked_button:
            ok = await _send_message(page, user_msg)
            if not ok:
                actual = "(ERROR: chat input not found)"
                eq.put({"type": "pw_turn_bot", "test_id": test["test_id"],
                        "turn": i + 1, "message": actual})
                turns_out.append({"turn": i + 1, "user": user_msg,
                                   "expected": expected, "actual": actual})
                break

        # ── Verify user bubble rendered (soft check, NO retry) ────────────────
        # Do NOT resend if the bubble isn't immediately visible — the message
        # was already sent successfully. Resending causes duplicate inputs.
        rendered = await _verify_user_bubble_rendered(page, prev_user_bubble,
                                                      timeout_sec=4.0)
        if rendered:
            prev_user_bubble = await _get_user_bubble_count(page)
        elif not clicked_button:
            print(f"[pw] turn {i+1}: user bubble not detected — proceeding anyway")

        # ── Wait for response (DOM-order primary, content-diff fallback) ────
        next_idx = i + 1
        next_user = turns[next_idx]["user"] if next_idx < len(turns) else ""

        async def _pre_type_next():
            if next_user and "{{OTP}}" not in next_user:
                ok = await _pretype_input(page, next_user)
                if ok:
                    print(f"[pw] turn {i+1}: pre-typed next input")

        response_texts = await _wait_for_response(
            page, user_msg=user_msg, start_index=start_index,
            on_thinking_start=_pre_type_next,
            pre_send_texts=pre_send_texts,
            is_button_click=clicked_button,
            is_first_turn=(i == 0),
        )

        # ── Strip greeting from first turn if it slipped past the filter ─────
        response_texts = _strip_greeting_from_response(response_texts, is_first_turn=(i == 0))

        # ── Append carousel (structured) and quick-reply buttons ─────────────
        # Carousels come first because we may need to strip their button labels
        # from the global button list to avoid duplication.
        # Pass user_msg so the carousel capture anchors to the current turn.
        cards = await _capture_carousel(page, user_msg=user_msg)

        # Collect quick-reply / page-level button labels SCOPED to this turn —
        # only buttons that appear after the current user bubble in the DOM.
        # Falls back to global button list if scoped capture returns nothing
        # and there's no user_msg to anchor to.
        labels = await _get_button_labels_after_user(page, user_msg)
        if not labels and not user_msg:
            labels = await _get_button_labels(page)
        if cards:
            card_btn_set = {b.strip().lower()
                            for c in cards for b in c.get("buttons", []) if b}
            labels = [l for l in labels if l.strip().lower() not in card_btn_set]

        # If the response_texts include text that exactly equals or is fully
        # contained in a card's structured fields, drop it — the card already
        # represents that content and we don't want to duplicate.
        if cards and response_texts:
            card_text_blobs = []
            for c in cards:
                blob = " ".join(filter(None, [
                    c.get("title", ""), c.get("subtitle", ""),
                    *c.get("buttons", []),
                ])).lower()
                card_text_blobs.append(blob)
            def _is_card_dup(t: str) -> bool:
                tl = " ".join(t.split()).lower()
                if len(tl) < 5:
                    return False
                for blob in card_text_blobs:
                    # treat as dup if the bot text is dominated by card content
                    overlap = sum(1 for tok in tl.split() if tok in blob)
                    if overlap and overlap / max(1, len(tl.split())) > 0.85:
                        return True
                return False
            response_texts = [t for t in response_texts if not _is_card_dup(t)]

        actual = " | ".join(response_texts) if response_texts else "(no response captured)"

        if cards:
            n = len(cards)
            actual += f"\n[carousel ({n} card{'s' if n != 1 else ''}):"
            for c in cards:
                actual += "\n - " + _format_card(c)
            actual += "]"
        if labels:
            actual = actual + "\n[buttons: " + " | ".join(labels) + "]"

        # ── Single-agent scope guardrail ─────────────────────────────────────
        is_boundary = (test.get("category", "") == "boundary_route")
        if single_agent_name and not is_boundary:
            joined = " ".join(response_texts)
            match = ROUTE_RE.search(joined)
            if match:
                routed_to = match.group(1).strip()
                if routed_to.lower() != single_agent_name.strip().lower():
                    scope_violation = (
                        f"Bot routed to [{routed_to}] which is outside the "
                        f"selected agent scope ([{single_agent_name}])."
                    )
                    actual = actual + f"\n(framework: routed outside scope → {routed_to})"
                    eq.put({"type": "pw_turn_bot", "test_id": test["test_id"],
                            "turn": i + 1, "message": actual})
                    turns_out.append({"turn": i + 1, "user": user_msg,
                                       "expected": expected, "actual": actual})
                    break

        eq.put({"type": "pw_turn_bot", "test_id": test["test_id"],
                "turn": i + 1, "message": actual})
        turns_out.append({"turn": i + 1, "user": user_msg,
                          "expected": expected, "actual": actual})

    return turns_out, scope_violation


# ── Session orchestrator ──────────────────────────────────────────────────────

async def _run_session(run_id: int, tests: list, bot_id: str, mode: str,
                       eq: queue.Queue, stop_flags: set, client, model: str,
                       otp_queue: queue.Queue | None = None,
                       single_agent_name: str | None = None,
                       other_agent_context: dict | None = None):
    """
    Launch a browser and run all tests sequentially.

    Session strategy: NEW browser context per test. This guarantees a completely
    fresh session (no shared cookies, localStorage, or IndexedDB) between tests.
    Yellow.ai ties conversations to client-side identity stored in the widget
    iframe's origin — a new context is the only 100% reliable way to reset.
    """
    from playwright.async_api import async_playwright
    import runner as r
    import db

    bot_url = BOT_URL.format(bot_id=bot_id)
    passed  = 0
    failed  = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=(mode == "headless"),
            slow_mo=0,
        )

        try:
            for test in tests:
                if run_id in stop_flags:
                    break

                tc_id   = test.get("test_id", "PT-?")
                uc_id   = test.get("use_case_id")
                uc_name = test.get("use_case_name", "")

                eq.put({"type": "pw_test_start", "test_id": tc_id,
                        "name": test.get("name", ""), "run_id": run_id,
                        "use_case_name": uc_name,
                        "category": test.get("category", "")})

                turns_result: list[dict] = []
                scope_violation: str | None = None
                error_msg: str | None = None

                # New context per test — guarantees no session bleed.
                # Grant geolocation so "Share My Location" buttons work.
                # Real Chrome UA prevents Yellow.ai "Connection lost" in headless mode.
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    geolocation={"latitude": 12.9716, "longitude": 77.5946},
                    permissions=["geolocation"],
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()

                try:
                    await _prepare_page_for_test(page, bot_url)

                    turns_result, scope_violation = await _run_one_test(
                        page, test, eq, otp_queue=otp_queue,
                        single_agent_name=single_agent_name,
                    )
                except Exception as exc:
                    error_msg = str(exc)
                finally:
                    try:
                        await ctx.close()
                    except Exception:
                        pass

                # ── Evaluation ───────────────────────────────────────────────
                if error_msg is not None:
                    overall = "ERROR"
                    summary = error_msg
                    ev = {"overall": overall, "summary": summary,
                          "turn_verdicts": [], "criteria_results": [],
                          "behavior_results": []}
                elif scope_violation:
                    overall = "FAIL"
                    summary = scope_violation
                    ev = {"overall": overall, "summary": summary,
                          "turn_verdicts": [], "criteria_results": [],
                          "behavior_results": []}
                else:
                    eq.put({"type": "pw_eval_start", "test_id": tc_id})
                    ev = r.evaluate_playwright_transcript(
                        turns_result,
                        test.get("pass_criteria", []),
                        client, model,
                        behavior_expectations=test.get("agent_behavior_expectations", []),
                        other_agent_context=other_agent_context,
                        use_case_id=uc_id,
                        test_id=tc_id,
                    )
                    overall = ev.get("overall", "ERROR")
                    summary = ev.get("summary", "")

                if overall == "PASS":
                    passed += 1
                else:
                    failed += 1

                db.save_playwright_result(
                    run_id, uc_id, uc_name, tc_id, test.get("name", ""),
                    [{"turn": t["turn"], "user": t["user"],
                      "expected": t["expected"], "actual": t["actual"]}
                     for t in turns_result],
                    overall, summary,
                    turn_verdicts=ev.get("turn_verdicts", []),
                    criteria_results=ev.get("criteria_results", []),
                    behavior_results=ev.get("behavior_results", []),
                    pass_criteria=test.get("pass_criteria", []),
                    behavior_expectations=test.get("agent_behavior_expectations", []),
                    category=test.get("category", ""),
                )
                db.update_playwright_run(run_id, passed=passed, failed=failed)

                eq.put({
                    "type": "pw_test_complete",
                    "test_id": tc_id, "name": test.get("name", ""),
                    "run_id": run_id, "use_case_id": uc_id, "use_case_name": uc_name,
                    "category": test.get("category", ""),
                    "overall": overall,
                    "summary": summary,
                    "turn_verdicts":    ev.get("turn_verdicts", []),
                    "criteria_results": ev.get("criteria_results", []),
                    "behavior_results": ev.get("behavior_results", []),
                    "turns": turns_result,
                })
        finally:
            try:
                await browser.close()
            except Exception:
                pass


# ── Public entry point (called from Flask thread) ─────────────────────────────

def start_playwright_run(run_id: int, tests: list, bot_id: str, mode: str,
                          eq: queue.Queue, stop_flags: set, client, model: str,
                          otp_queue: queue.Queue | None = None,
                          single_agent_name: str | None = None,
                          other_agent_context: dict | None = None):
    """
    Synchronous wrapper — creates a fresh asyncio event loop, runs the
    Playwright session to completion, then puts the pw_done sentinel.
    """
    import sys, traceback
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_session(run_id, tests, bot_id, mode, eq, stop_flags, client, model,
                         otp_queue=otp_queue,
                         single_agent_name=single_agent_name,
                         other_agent_context=other_agent_context)
        )
    except BaseException as exc:
        # Catch BaseException so KeyboardInterrupt / CancelledError surface too.
        tb = traceback.format_exc()
        print(f"[pw_runner] run {run_id} crashed: {type(exc).__name__}: {exc}\n{tb}",
              flush=True, file=sys.stderr)
        try:
            eq.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
    finally:
        try:
            loop.close()
        except Exception:
            pass
        try:
            eq.put({"type": "pw_done", "run_id": run_id})
        except Exception:
            pass
