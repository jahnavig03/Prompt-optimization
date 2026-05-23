"""
knowledge_store.py — per-agent knowledge base for the evaluator.

For each use case (agent), persists:
  knowledge/agent_<uc_id>/accepted_tests.json   ← uploaded/exported passing runs
  knowledge/agent_<uc_id>/rubric.md             ← LLM-derived SOP for the judge

The rubric is rebuilt (merge + dedupe via LLM) after every upload and after
every acceptance-rule add/edit/delete.  It saturates over time into the
agent's effective SOP and is injected into every evaluator prompt.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import db

BASE_DIR        = Path(__file__).parent
KNOWLEDGE_ROOT  = BASE_DIR / "knowledge"

# Hard caps to keep prompt-injection costs bounded.
MAX_TESTS_IN_PROMPT  = 12     # we summarise into rubric; raw tests are reference
MAX_RUBRIC_CHARS     = 6000


# ── Schema ────────────────────────────────────────────────────────────────────

ACCEPTED_TESTS_SCHEMA_VERSION = 1

# Canonical shape persisted to disk:
# {
#   "schema_version": 1,
#   "agent": "Book a Ride",
#   "bot_id": "x...",
#   "use_case_id": 42,
#   "exported_at": "2026-05-22T...",
#   "accepted_tests": [
#     {
#       "id": "PT-003",
#       "name": "...",
#       "judge": "...",
#       "turns": [{"user": "...", "expected": "...", "bot": "..."}]
#     }, ...
#   ]
# }


def _agent_dir(uc_id: int) -> Path:
    d = KNOWLEDGE_ROOT / f"agent_{uc_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _accepted_path(uc_id: int) -> Path:
    return _agent_dir(uc_id) / "accepted_tests.json"


def _rubric_path(uc_id: int) -> Path:
    return _agent_dir(uc_id) / "rubric.md"


# ── Persistence ───────────────────────────────────────────────────────────────

def load_accepted_tests(uc_id: int) -> dict:
    p = _accepted_path(uc_id)
    if not p.exists():
        return {
            "schema_version": ACCEPTED_TESTS_SCHEMA_VERSION,
            "use_case_id":    uc_id,
            "accepted_tests": [],
        }
    try:
        return json.loads(p.read_text())
    except Exception:
        return {
            "schema_version": ACCEPTED_TESTS_SCHEMA_VERSION,
            "use_case_id":    uc_id,
            "accepted_tests": [],
        }


def save_accepted_tests(uc_id: int, payload: dict) -> dict:
    """
    Validate + normalise + write payload to disk.  Returns the saved doc.
    Merges with existing tests (dedupe by id) instead of overwriting.
    """
    incoming = _validate_payload(payload)

    existing = load_accepted_tests(uc_id)
    by_id    = {t["id"]: t for t in existing.get("accepted_tests", [])}
    for t in incoming["accepted_tests"]:
        by_id[t["id"]] = t       # incoming wins on conflict

    merged = {
        "schema_version": ACCEPTED_TESTS_SCHEMA_VERSION,
        "use_case_id":    uc_id,
        "agent":          incoming.get("agent")  or existing.get("agent")  or "",
        "bot_id":         incoming.get("bot_id") or existing.get("bot_id") or "",
        "exported_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accepted_tests": list(by_id.values()),
    }
    _accepted_path(uc_id).write_text(json.dumps(merged, indent=2))
    return merged


def replace_accepted_tests(uc_id: int, payload: dict) -> dict:
    """Replace (don't merge) — exposed if the user explicitly wants a clean reset."""
    incoming = _validate_payload(payload)
    incoming["schema_version"] = ACCEPTED_TESTS_SCHEMA_VERSION
    incoming["use_case_id"]    = uc_id
    incoming["exported_at"]    = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _accepted_path(uc_id).write_text(json.dumps(incoming, indent=2))
    return incoming


def _validate_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Knowledge file must be a JSON object.")
    tests = payload.get("accepted_tests")
    if not isinstance(tests, list):
        raise ValueError("Missing or invalid 'accepted_tests' array.")

    cleaned = []
    for i, t in enumerate(tests):
        if not isinstance(t, dict):
            continue
        tid = (t.get("id") or t.get("test_id") or f"AT-{i+1:03d}").strip()
        turns_raw = t.get("turns") or t.get("conversation") or []
        if not isinstance(turns_raw, list):
            continue
        turns = []
        for trn in turns_raw:
            if not isinstance(trn, dict):
                continue
            turns.append({
                "user":     str(trn.get("user", "")).strip(),
                "expected": str(trn.get("expected", "")).strip(),
                "bot":      str(trn.get("bot", "")).strip(),
            })
        if not turns:
            continue
        cleaned.append({
            "id":    tid,
            "name":  str(t.get("name", "")).strip(),
            "judge": str(t.get("judge") or t.get("criterion") or "").strip(),
            "turns": turns,
        })
    return {
        "agent":          str(payload.get("agent", "")).strip(),
        "bot_id":         str(payload.get("bot_id", "")).strip(),
        "accepted_tests": cleaned,
    }


def load_rubric(uc_id: int) -> str:
    p = _rubric_path(uc_id)
    return p.read_text() if p.exists() else ""


def save_rubric(uc_id: int, text: str) -> None:
    _rubric_path(uc_id).write_text(text)


def clear(uc_id: int) -> None:
    for p in (_accepted_path(uc_id), _rubric_path(uc_id)):
        if p.exists():
            p.unlink()


# ── Rubric generation ────────────────────────────────────────────────────────

RUBRIC_SYSTEM = """You author judging rubrics for an LLM evaluator that grades
a customer-support chatbot.  You receive two evidence streams for ONE agent:

  A) ACCEPTED TEST CASES — conversations a human has verified as correct.
  B) ACCEPTANCE RULES — one-line corrections the human added whenever the LLM
     judge was too strict in past evaluations.

Your job: distil both into a clean, deduplicated SOP-style rubric the judge
must apply before marking any future turn PASS or FAIL.

Rules for the rubric you produce:
  - Output Markdown only.  No preamble, no explanation, no greeting.
  - Group items into short numbered sections (max ~7 sections, max ~5 bullets
    each).  Be ruthless about merging duplicates and near-duplicates.
  - Each bullet is one sentence stating a concrete check the judge must apply.
  - Prefer behavioural language ("bot may rephrase X as Y") over surface
    matching ("bot must say exactly X").
  - If an acceptance rule (B) covers the same behaviour as an accepted test
    case (A), keep the rule and drop the test case — human corrections
    always take priority over historical test evidence.
  - If two pieces of evidence contradict, prefer the acceptance rule (B)
    first, then the more recent / more specific one, and drop the older.
  - Never invent rules that aren't grounded in the evidence.
  - Keep the entire rubric under 1500 words.
  - Start with a one-line header: `# Judging Rubric — <agent>` then the
    sections.
"""


def rebuild_rubric(uc_id: int, client, model: str) -> str:
    """
    Re-derive the rubric from accepted_tests.json + db.acceptance_rules.
    Writes rubric.md and returns its text.  Returns "" if there's no evidence.
    """
    doc            = load_accepted_tests(uc_id)
    accepted_tests = doc.get("accepted_tests", [])
    try:
        accept_rules = db.list_acceptance_rules(uc_id, active_only=True)
    except Exception:
        accept_rules = []

    if not accepted_tests and not accept_rules:
        # No evidence at all — clear any stale rubric so we don't keep using it.
        save_rubric(uc_id, "")
        return ""

    agent_name = doc.get("agent", "")

    # Build evidence prompt
    parts: list[str] = []
    parts.append(f"AGENT: {agent_name or '(unnamed)'}\n")

    if accepted_tests:
        parts.append("\n## A) ACCEPTED TEST CASES\n")
        for t in accepted_tests[:MAX_TESTS_IN_PROMPT]:
            parts.append(f"\n### {t['id']} — {t.get('name','')}")
            if t.get("judge"):
                parts.append(f"_Judge criterion_: {t['judge']}")
            for i, trn in enumerate(t["turns"], 1):
                parts.append(f"- Turn {i} user: {trn['user']}")
                if trn.get("expected"):
                    parts.append(f"  expected: {trn['expected']}")
                parts.append(f"  bot: {trn['bot']}")
        if len(accepted_tests) > MAX_TESTS_IN_PROMPT:
            parts.append(f"\n_(...and {len(accepted_tests) - MAX_TESTS_IN_PROMPT} "
                         f"more test cases not shown)_")

    if accept_rules:
        parts.append("\n## B) ACCEPTANCE RULES (human corrections)\n")
        for r in accept_rules:
            scope = r.get("scope") or "agent"
            tid   = r.get("test_id") or r.get("source_test_id") or "*"
            parts.append(f"- [{scope}/{tid}] {r['human_rule']}")
            # Include the full conversation if available
            conv_raw = r.get("conversation_turns") or "[]"
            try:
                conv = json.loads(conv_raw) if isinstance(conv_raw, str) else conv_raw
            except Exception:
                conv = []
            if conv and isinstance(conv, list):
                for i, t in enumerate(conv, 1):
                    if isinstance(t, dict):
                        parts.append(f"    Turn {i} user: {t.get('user', '')}")
                        if t.get("expected"):
                            parts.append(f"    expected: {t['expected']}")
                        parts.append(f"    bot: {t.get('bot', '')}")

    user_msg = "\n".join(parts)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": RUBRIC_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        # If rebuild fails, leave the existing rubric in place (don't wipe).
        existing = load_rubric(uc_id)
        if existing:
            return existing
        text = f"# Judging Rubric — {agent_name or 'agent'}\n\n" \
               f"_(rubric generation failed: {e})_"

    if len(text) > MAX_RUBRIC_CHARS:
        text = text[:MAX_RUBRIC_CHARS] + "\n\n_(truncated)_"

    save_rubric(uc_id, text)
    return text


# ── Prompt-injection helper ──────────────────────────────────────────────────

def format_for_evaluator(uc_id: int | None) -> str:
    """
    Return the block to splice into every evaluator prompt.  Empty string if
    no rubric exists.  Call this from EVERY evaluator (simulation + Playwright)
    so judgement is consistent across runs.
    """
    if not uc_id:
        return ""
    rubric = load_rubric(uc_id)
    if not rubric.strip():
        return ""
    return (
        "JUDGING RUBRIC — apply BEFORE marking any PASS/FAIL.\n"
        "This is the canonical SOP for this agent, derived from human-accepted\n"
        "test cases and human corrections of past evaluations.  When the rubric\n"
        "addresses a behaviour, the rubric wins over your prior assumptions.\n\n"
        f"{rubric}\n\n---\n\n"
    )


# ── Export helper (used by web_app to ship a run as JSON) ────────────────────

def export_run_as_payload(uc_id: int, run_id: int, agent_name: str,
                          bot_id: str, accepted_only: bool = True) -> dict:
    """
    Build the upload-ready payload from a completed Playwright run.  The
    caller writes it to a file or hands it to save_accepted_tests().
    """
    rows = db.get_playwright_results(run_id)
    tests = []
    for r in rows or []:
        overall = (r.get("overall") or "").upper()
        if accepted_only and overall != "PASS":
            continue
        # get_playwright_results already parses turns to a list.
        turns_raw = r.get("turns") or []
        if not isinstance(turns_raw, list):
            continue
        turns = [{
            "user":     (t.get("user") or "").strip(),
            "expected": (t.get("expected") or "").strip(),
            "bot":      (t.get("actual") or t.get("bot") or "").strip(),
        } for t in turns_raw if isinstance(t, dict)]
        if not turns:
            continue
        tests.append({
            "id":    r.get("test_id", ""),
            "name":  r.get("name", ""),
            "judge": (r.get("summary") or "").strip(),
            "turns": turns,
        })
    return {
        "schema_version": ACCEPTED_TESTS_SCHEMA_VERSION,
        "agent":          agent_name,
        "bot_id":         bot_id,
        "use_case_id":    uc_id,
        "exported_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accepted_tests": tests,
    }


# ── PDF → JSON converter (used by scripts/pdf_to_json.py) ────────────────────

# The lab's report_pdf.py emits very predictable text:
#   <ID> — <name>   PASS|FAIL
#   Agent: <agent>
#   Judge: <criterion>
#   CONVERSATION
#   Turn N — User
#   <user msg>
#   Expected: <expected>
#   Bot
#   <bot reply>

_TEST_HEADER_RE  = re.compile(r"^(PT-\d+|TC-\d+|[A-Z]{2,4}-\d+)\s*[—\-–]\s*(.+?)\s+(PASS|FAIL)\s*$",
                              re.MULTILINE)
_TURN_HEADER_RE  = re.compile(r"^Turn\s+(\d+)\s*[—\-–]\s*User\s*$", re.MULTILINE)


def parse_lab_pdf(pdf_path: str) -> dict:
    """
    Parse a 'Live Bot Test Report' PDF produced by report_pdf.py and return
    the same payload shape save_accepted_tests expects.  Only PASS tests
    are included.
    """
    try:
        import pypdf
    except ImportError as e:
        raise RuntimeError("Install pypdf to use the PDF converter: "
                           "`pip install pypdf`") from e

    reader = pypdf.PdfReader(pdf_path)
    full   = "\n".join(p.extract_text() or "" for p in reader.pages)

    # Strip page footers
    full = re.sub(r"Generated by Prompt Optimization Lab\s*\n*Page \d+/\d+",
                  "", full)

    agent  = ""
    bot_id = ""
    m = re.search(r"Bot ID:\s*\n?\s*(\S+)", full)
    if m: bot_id = m.group(1).strip()
    m = re.search(r"Agent:\s*\n?\s*([^\n]+)", full)
    if m: agent = m.group(1).strip()

    # Split into test-case chunks
    headers = list(_TEST_HEADER_RE.finditer(full))
    tests: list[dict] = []
    for idx, h in enumerate(headers):
        verdict = h.group(3)
        if verdict != "PASS":
            continue
        tid   = h.group(1)
        name  = h.group(2).strip()
        start = h.end()
        end   = headers[idx + 1].start() if idx + 1 < len(headers) else len(full)
        body  = full[start:end]

        judge_m = re.search(r"Judge:\s*([^\n]+)", body)
        judge   = judge_m.group(1).strip() if judge_m else ""

        # Conversation parsing
        conv_m = re.search(r"CONVERSATION", body)
        conv = body[conv_m.end():] if conv_m else body
        turns = _parse_turns(conv)
        if not turns:
            continue
        tests.append({
            "id":    tid,
            "name":  name,
            "judge": judge,
            "turns": turns,
        })

    # Dedupe by id (PDF may render the same case multiple times across pages).
    # Prefer the LAST occurrence, which is typically the fully rendered version.
    by_id: dict[str, dict] = {}
    for t in tests:
        by_id[t["id"]] = t
    deduped = list(by_id.values())

    return {
        "schema_version": ACCEPTED_TESTS_SCHEMA_VERSION,
        "agent":          agent,
        "bot_id":         bot_id,
        "exported_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accepted_tests": deduped,
    }


def _parse_turns(conv_text: str) -> list[dict]:
    turn_starts = list(_TURN_HEADER_RE.finditer(conv_text))
    turns: list[dict] = []
    for i, ts in enumerate(turn_starts):
        end = turn_starts[i + 1].start() if i + 1 < len(turn_starts) else len(conv_text)
        block = conv_text[ts.end():end]

        # User text: everything until "Expected:" or "Bot"
        user_m = re.split(r"\n\s*Expected:|\n\s*Bot\s*\n", block, maxsplit=1)
        user_text = user_m[0].strip() if user_m else ""

        # Expected
        exp_m = re.search(r"Expected:\s*(.+?)(?=\n\s*Bot\s*\n|$)",
                          block, re.DOTALL)
        expected = exp_m.group(1).strip() if exp_m else ""

        # Bot
        bot_m = re.search(r"\n\s*Bot\s*\n(.+?)$", block, re.DOTALL)
        bot   = bot_m.group(1).strip() if bot_m else ""

        if user_text or bot:
            turns.append({
                "user":     _collapse_ws(user_text),
                "expected": _collapse_ws(expected),
                "bot":      _collapse_ws(bot),
            })
    return turns


def _collapse_ws(s: str) -> str:
    # Re-join PDF line breaks that split sentences mid-line
    s = re.sub(r"\s*\n\s*", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()
