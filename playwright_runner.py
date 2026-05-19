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

YAI_BOT_MSG_SEL = "[class*='yai-group'][class*='yai-items-start']"

YAI_USER_MSG_SELECTORS = [
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
    const groups = Array.from(document.querySelectorAll('[class*="yai-group"]'));
    const out = [];
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
        const txt = (g.innerText || '').trim();
        out.push({ role: isUser ? 'user' : 'bot', text: txt });
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
    Used to filter out welcome messages and replayed history from response capture."""
    msgs = await _get_all_messages(page)
    return {_norm(m.get("text") or "") for m in msgs
            if m.get("role") == "bot" and len((m.get("text") or "").strip()) > 1}


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
    """Remove greeting messages from a multi-message bot response on the first turn.
    Only strips if there are multiple messages AND the first one is a greeting —
    never strips the only response message."""
    if not is_first_turn or len(texts) <= 1:
        return texts
    if _is_greeting(texts[0]):
        return texts[1:]
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
    """Click ANY visible button matching label (flexible matching).
    Strips brackets from test script actions and uses fuzzy matching to handle
    slight wording differences between test scripts and actual button text.
    For bracketed actions like [Shares live location], extracts keywords and
    matches against visible buttons using stemming."""
    is_action = _is_bracketed_action(label)
    raw_target = _strip_brackets(label)
    for btn_label, btn_el in await _get_visible_buttons(page):
        if _fuzzy_button_match(raw_target, btn_label, is_action=is_action):
            try:
                await btn_el.click()
                print(f"[pw] clicked button '{btn_label}' (matched '{raw_target}')")
                return True
            except Exception:
                pass
    return False


async def _get_carousel_texts(page) -> list[str]:
    """
    Detect a multi-card carousel via JS evaluation inside the iframe.
    Returns [] if fewer than 2 distinct cards are found.
    """
    ym_frame = await _get_ym_frame(page)
    if not ym_frame:
        return []

    _JS_FIND_CAROUSEL = """() => {
        function collapseWS(s) { return (s || '').trim().replace(/\\s+/g, ' '); }
        function findCardGroup(root, depth) {
            if (!root || depth > 8) return null;
            const children = Array.from(root.children);
            if (children.length >= 2) {
                const tags = new Set(children.map(c => c.tagName));
                if (tags.size <= 2) {
                    const texts = children.map(c => collapseWS(c.textContent)).filter(t => t.length > 8);
                    const uniq = [...new Set(texts)];
                    if (uniq.length >= 2 && uniq.some(t => t.length > 20)) return uniq;
                }
            }
            for (const child of children) {
                if (child.children.length > 0) {
                    const found = findCardGroup(child, depth + 1);
                    if (found) return found;
                }
            }
            return null;
        }
        const botSelectors = [
            '[class*="yai-group"][class*="yai-items-start"]',
            '[class*="yai-items-start"]',
            '[class*="bot-message"]',
            '[class*="received"]',
        ];
        let lastGroup = null;
        for (const sel of botSelectors) {
            const matches = document.querySelectorAll(sel);
            if (matches.length > 0) { lastGroup = matches[matches.length - 1]; break; }
        }
        if (lastGroup) {
            const result = findCardGroup(lastGroup, 0);
            if (result && result.length >= 2) return result;
        }
        const allEls = Array.from(document.querySelectorAll('*'));
        for (const el of allEls) {
            const st = window.getComputedStyle(el);
            const isHScroll = st.overflowX === 'auto' || st.overflowX === 'scroll';
            const hasSnapClass = (el.className || '').includes('snap') ||
                                 (el.className || '').includes('scroll') ||
                                 (el.className || '').includes('overflow');
            if (!isHScroll && !hasSnapClass) continue;
            const children = Array.from(el.children);
            if (children.length < 2) continue;
            const texts = children.map(c => collapseWS(c.textContent)).filter(t => t.length > 8);
            const uniq = [...new Set(texts)];
            if (uniq.length >= 2 && uniq.some(t => t.length > 20)) return uniq;
        }
        return [];
    }"""

    try:
        cards: list = await ym_frame.evaluate(_JS_FIND_CAROUSEL)
        if cards and len(cards) >= 2:
            seen: set[str] = set()
            return [c for c in cards if c and not (c in seen or seen.add(c))]
    except Exception as e:
        print(f"[pw] carousel JS eval error: {e}")

    for sel in CAROUSEL_CARD_SELECTORS:
        try:
            els = await ym_frame.locator(sel).all()
            if len(els) < 2:
                continue
            texts2: list[str] = []
            for el in els:
                try:
                    t = (await el.text_content() or "").strip()
                    t = " ".join(t.split())
                    if t and len(t) > 5:
                        texts2.append(t)
                except Exception:
                    pass
            seen2: set[str] = set()
            uniq2 = [t for t in texts2 if not (t in seen2 or seen2.add(t))]
            if len(uniq2) >= 2:
                return uniq2
        except Exception:
            pass
    return []


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
                              is_button_click: bool = False) -> list[str]:
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
        if not _pre:
            return texts
        filtered = [t for t in texts if _norm(t) not in _pre]
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
            await page.goto(bot_url, wait_until="load", timeout=45_000)
        except Exception as e:
            last_err = f"navigation failed: {e}"
            print(f"[pw] _prepare_page (attempt {attempt}): {last_err}")
            await asyncio.sleep(1.0)
            continue

        iframe_seen = False
        for _ in range(30):
            if await _get_ym_frame(page):
                iframe_seen = True
                break
            await asyncio.sleep(0.3)

        if not iframe_seen:
            last_err = "widget iframe never loaded"
            print(f"[pw] _prepare_page (attempt {attempt}): {last_err}")
            await asyncio.sleep(1.0)
            continue

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
    # replayed history have rendered. The snapshot taken before each send will
    # exclude these from the captured response.
    await asyncio.sleep(0.5)
    await _wait_for_widget_idle(page, max_wait_sec=15.0, stable_window=2.0)
    prev_user_bubble = await _get_user_bubble_count(page)

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
        # be filtered from the response (handles welcome msgs + replayed history).
        start_index = await _count_message_groups(page)
        pre_send_texts = await _snapshot_bot_texts(page)
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
        )

        # ── Strip greeting from first turn if it slipped past the filter ─────
        response_texts = _strip_greeting_from_response(response_texts, is_first_turn=(i == 0))

        # ── Append buttons and carousel ─────────────────────────────────────
        labels = await _get_button_labels(page)
        carousel = await _get_carousel_texts(page)
        actual = " | ".join(response_texts) if response_texts else "(no response captured)"
        if labels:
            actual = actual + "\n[buttons: " + " | ".join(labels) + "]"
        if carousel:
            actual = (actual + f"\n[carousel cards ({len(carousel)}):\n - "
                      + "\n - ".join(carousel) + "]")

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
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    geolocation={"latitude": 12.9716, "longitude": 77.5946},
                    permissions=["geolocation"],
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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_session(run_id, tests, bot_id, mode, eq, stop_flags, client, model,
                         otp_queue=otp_queue,
                         single_agent_name=single_agent_name,
                         other_agent_context=other_agent_context)
        )
    finally:
        loop.close()
        eq.put({"type": "pw_done", "run_id": run_id})
