"""runner.py — Test runner, evaluator, test generator, and optimization loop."""

import copy
import difflib
import json
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

import db

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "lab_config.json"
GUIDES_DIR  = BASE_DIR / "Prompt Guides"

# ── Platform writing guides (loaded once at startup) ──────────────────────────
_GUIDE_FILES = [
    ("Agent Writing Guide",         "agent-prompt-guide.md"),
    ("Complex Step-Based Agents",   "complex-steps-guide.md"),
    ("Tool Creation Guide",         "tools-guide.md"),
    ("V3 Engine — Platform Guide",  "v3-engine-guide.md"),
]


def _load_guides() -> str:
    parts = []
    for title, fname in _GUIDE_FILES:
        f = GUIDES_DIR / fname
        if f.exists():
            parts.append(f"# {title}\n\n{f.read_text().strip()}")
    return "\n\n---\n\n".join(parts)


PROMPT_GUIDES = _load_guides()

# ── Run registry (in-memory per server lifetime) ──────────────────────────────
_run_queues: dict[int, queue.Queue] = {}
_step_events: dict[int, threading.Event] = {}
_stop_flags: set[int] = set()


def get_queue(run_id: int) -> queue.Queue | None:
    return _run_queues.get(run_id)


def register_run(run_id: int) -> queue.Queue:
    eq = queue.Queue()
    _run_queues[run_id] = eq
    _step_events[run_id] = threading.Event()
    _step_events[run_id].set()  # starts as "go"
    return eq


def signal_continue(run_id: int):
    if run_id in _step_events:
        _step_events[run_id].set()


def signal_stop(run_id: int):
    _stop_flags.add(run_id)
    if run_id in _step_events:
        _step_events[run_id].set()


def _should_stop(run_id: int) -> bool:
    return run_id in _stop_flags


# ── LLM client helpers ────────────────────────────────────────────────────────

def _make_client() -> tuple[OpenAI, str]:
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except Exception:
        cfg = {}
    api_key = cfg.get("openai_api_key", "")
    model   = cfg.get("openai_model", "gpt-4.1") or "gpt-4.1"
    return OpenAI(api_key=api_key), model


# ── Tool definitions ──────────────────────────────────────────────────────────

_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_tool_name(name: str) -> str:
    s = _TOOL_NAME_RE.sub("_", (name or "").strip())
    s = s.strip("_") or "tool"
    return s[:64]


def build_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": sanitize_tool_name(t["name"]),
                "description": t.get("description", ""),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        for t in tools
    ]


# ── Mock resolution ───────────────────────────────────────────────────────────

def resolve_mock(tool_name: str, mock_overrides: dict) -> dict:
    if tool_name in mock_overrides:
        return copy.deepcopy(mock_overrides[tool_name])
    for k, v in (mock_overrides or {}).items():
        if sanitize_tool_name(k) == tool_name:
            return copy.deepcopy(v)
    return {"message": "The request has been successfully submitted for processing", "status": "success"}


# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(frontend_prompt: str) -> str:
    backend_file = BASE_DIR / "backend_prompt.md"
    backend = backend_file.read_text().strip() if backend_file.exists() else ""
    return backend + "\n\n---\n\n## AGENT INSTRUCTIONS (FRONTEND PROMPT)\n\n" + frontend_prompt.strip()


# ── Conversation runner ───────────────────────────────────────────────────────

EVALUATOR_SYSTEM = """You are a strict, objective test evaluator for a customer support AI bot.
For each criterion, respond PASS or FAIL. PASS only if clearly and unambiguously satisfied.
- "Tool X is called" = tool name appears in the tool_calls list
- "Tool X is NOT called" = tool name does NOT appear in the tool_calls list
- "Bot routes to [AgentX]" = [ROUTE TO: AgentX] appears in a bot message
- "Bot response contains NO human-readable text other than the routing marker" = PASS only if the bot message is literally only [ROUTE TO:...] with no other sentences

Respond ONLY with this JSON (no markdown):
{"results": [{"criterion": "...", "verdict": "PASS|FAIL", "reason": "one sentence"}], "overall": "PASS|FAIL", "summary": "one sentence"}
overall = PASS only if ALL criteria PASS."""


def run_conversation(tc_id: str, script: list[str], mock_overrides: dict,
                     system_prompt: str, openai_tools: list,
                     client: OpenAI, model: str, eq: queue.Queue,
                     run_id: int | None = None) -> dict:
    messages    = [{"role": "system", "content": system_prompt}]
    call_counts: dict[str, int] = {}
    transcript  = []

    def _stopped() -> bool:
        return run_id is not None and _should_stop(run_id)

    for turn_idx, user_msg in enumerate(script):
        if _stopped():
            return {"transcript": transcript, "tool_calls_made": call_counts, "stopped_reason": "stopped"}
        eq.put({"type": "turn_user", "tc_id": tc_id, "turn": turn_idx + 1, "message": user_msg})
        messages.append({"role": "user", "content": user_msg})

        for _ in range(15):
            if _stopped():
                return {"transcript": transcript, "tool_calls_made": call_counts, "stopped_reason": "stopped"}
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages,
                    tools=openai_tools, tool_choice="auto", temperature=0
                )
            except Exception as e:
                eq.put({"type": "error", "tc_id": tc_id, "message": str(e)})
                return {"transcript": transcript, "tool_calls_made": call_counts, "error": str(e)}

            if _stopped():
                return {"transcript": transcript, "tool_calls_made": call_counts, "stopped_reason": "stopped"}

            msg = resp.choices[0].message
            entry: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            messages.append(entry)

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tname = tc.function.name
                    call_counts[tname] = call_counts.get(tname, 0) + 1
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        args = {}
                    mock = resolve_mock(tname, mock_overrides)
                    eq.put({"type": "tool_call", "tc_id": tc_id, "turn": turn_idx + 1,
                            "tool": tname, "args": args, "result": mock})
                    transcript.append({"type": "tool_call", "turn": turn_idx + 1,
                                       "tool": tname, "args": args, "result": mock})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(mock)})
            else:
                bot_text = msg.content or ""
                eq.put({"type": "turn_bot", "tc_id": tc_id, "turn": turn_idx + 1, "message": bot_text})
                transcript.append({"type": "bot_message", "turn": turn_idx + 1,
                                   "user": user_msg, "bot": bot_text})
                if "[ROUTE TO:" in bot_text:
                    return {"transcript": transcript, "tool_calls_made": call_counts, "stopped_reason": "routing"}
                break

        time.sleep(0.2)

    return {"transcript": transcript, "tool_calls_made": call_counts, "stopped_reason": "completed"}


def evaluate_transcript(result: dict, criteria: list[str], client: OpenAI, model: str) -> dict:
    if not criteria:
        return {"overall": "SKIP", "results": [], "summary": "No criteria defined", "criteria": []}

    lines = []
    for e in result["transcript"]:
        if e.get("type") == "tool_call":
            lines.append(f"[TOOL CALL] {e['tool']}({json.dumps(e['args'])}) → {json.dumps(e['result'])[:300]}")
        elif e.get("type") == "bot_message":
            lines.append(f"User (Turn {e['turn']}): {e['user']}")
            lines.append(f"Bot  (Turn {e['turn']}): {e['bot']}")

    prompt = (
        f"TRANSCRIPT:\n{chr(10).join(lines)}\n\n"
        f"TOOL CALLS MADE: {json.dumps(result.get('tool_calls_made', {}))}\n\n"
        f"PASS CRITERIA:\n" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))
    )

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": EVALUATOR_SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        ev = json.loads(r.choices[0].message.content)
        ev["criteria"] = criteria
        return ev
    except Exception as e:
        return {"overall": "ERROR", "summary": str(e), "results": [], "criteria": criteria}


# ── Test generation ───────────────────────────────────────────────────────────

GENERATE_SYSTEM = """You are a senior QA engineer generating test cases for a customer support AI chatbot.

You receive business requirements AND (optionally) the current agent prompt. Use BOTH to produce a comprehensive set of test cases:
- Requirements define what the agent MUST do — every branch, rule, and constraint must have at least one test
- The agent prompt (if provided) reveals the exact steps, conditions, and tool calls the bot performs — use it to generate scenario-specific mock data and realistic conversation scripts

For each test case produce:
- "test_id": "TC-001", "TC-002", etc.
- "name": short label (under 70 chars)
- "conversation_script": array of CUSTOMER messages only (not bot replies)
- "pass_criteria": array of specific, objectively verifiable assertions about bot behavior
- "mock_overrides": object mapping tool_name → realistic mock response for this scenario, constrained by the tool's return schema. Include only tools that will actually be called in this scenario. Use the schema field types and allowed enum values strictly.

Rules:
- conversation_script is ONLY what the customer types — never bot messages
- pass_criteria assertions must be verifiable from a transcript by a third party — not vague ("handled well")
- mock_overrides must use field names and value types exactly matching the return schema
- Return JSON only: {"tests": [...]}"""


def generate_tests(requirements: str, sub_agents: list[dict], memory_keys: list[dict],
                   tools: list[dict], client: OpenAI, model: str,
                   prompt: str = "") -> list[dict]:
    sa_text  = "\n".join(f"- [{sa['name']}]: {sa.get('description','')}" for sa in sub_agents)
    mk_text  = "\n".join(f"- {{{{{mk['key_name']}}}}}: {mk.get('description','')}" for mk in memory_keys)
    tools_text = "\n\n".join(
        f"Tool: {t['name']}\nDescription: {t.get('description','')}\nReturn schema: {json.dumps(t.get('return_schema',{}), indent=2)}"
        for t in tools
    )

    prompt_section = f"CURRENT AGENT PROMPT:\n{prompt}\n\n" if prompt and prompt.strip() else ""

    user_msg = (
        f"BUSINESS REQUIREMENTS:\n{requirements}\n\n"
        f"{prompt_section}"
        f"SUB-AGENTS (routing destinations):\n{sa_text}\n\n"
        f"MEMORY KEYS:\n{mk_text}\n\n"
        f"TOOLS WITH RETURN SCHEMAS:\n{tools_text}\n\n"
        "Generate test cases covering all requirements and all branches visible in the agent prompt."
    )

    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": GENERATE_SYSTEM}, {"role": "user", "content": user_msg}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(r.choices[0].message.content).get("tests", [])


# ── Diagnosis & repair ────────────────────────────────────────────────────────

REPAIR_SYSTEM = """You are an expert prompt engineer optimizing a Yellow.ai V3 frontend agent prompt.

You will receive:
1. Yellow.ai platform writing guides — official documentation covering V3 agent structure, variable syntax, tool calls, and engine behavior. Read these first and follow them strictly.
2. A REFERENCE NAMES section listing the exact variable names, tool names, and agent names configured for this use case.
3. The current frontend prompt.
4. Failed test transcripts with their pass criteria and per-criterion verdicts.

Your job:
1. Study the platform guides to understand correct Yellow.ai V3 conventions (syntax, step anatomy, tool call format, routing syntax)
2. Diagnose the root cause of each failure (be specific — which instruction is missing, ambiguous, or contradicted?)
3. Produce a repaired prompt that fixes the failures without breaking passing tests
4. Make surgical edits only — do not rewrite sections that are working

SYNTAX RULES (platform-compatible — the prompt is copy-pasted directly into Yellow.ai):
- Variables: ALWAYS use {{variableName}} using the EXACT name from the REFERENCE NAMES list
- Tool/workflow calls: use the EXACT tool name from the REFERENCE NAMES list (e.g. Call toolName or @[workflow:slug])
- Agent routing: ALWAYS use [Exact Agent Name] using the EXACT name from the REFERENCE NAMES list — NEVER use slug IDs like @[agent:abc123]
- Never invent names not present in the REFERENCE NAMES list
- One action per step — never combine two actions in one step

FORMATTING RULES (match Yellow.ai's rich-text editor format exactly):
- Use # for top-level section titles (GOAL, RULES, GLOBAL VALIDATION RULES, etc.)
- Use ## for step headers: ## Step N — Descriptive Name
- Use ### for sub-sections within a step
- Use - bullet points for conditions, branching logic, and rules
- Wrap exact phrases the bot must say verbatim in code blocks: `Ask exactly: "..."`
- Keep blank lines between sections for readability
- Preserve the overall document structure of the input prompt

Respond with JSON only:
{"diagnosis": "concise root cause analysis (under 300 words)", "new_prompt": "complete repaired prompt text"}"""


def _build_reference_names(uc_config: dict) -> str:
    """Build the REFERENCE NAMES block injected into every repair call."""
    lines = ["REFERENCE NAMES — use ONLY these exact names, never slugs or invented alternatives:\n"]

    memory_keys = uc_config.get("memory_keys") or []
    if memory_keys:
        lines.append("Variables (reference as {{varName}}):")
        for mk in memory_keys:
            desc = f"  # {mk['description']}" if mk.get("description") else ""
            lines.append(f"  {{{{ {mk['key_name']} }}}}{desc}")
        lines.append("")

    tools = uc_config.get("tools") or []
    if tools:
        lines.append("Tools / Workflows (call by name — e.g. Call toolName):")
        for t in tools:
            desc = f"  # {t['description']}" if t.get("description") else ""
            lines.append(f"  {t['name']}{desc}")
        lines.append("")

    sub_agents = uc_config.get("sub_agents") or []
    if sub_agents:
        lines.append("Agent Routes (route as [Agent Name] — NEVER use @[agent:slug] format):")
        for sa in sub_agents:
            desc = f"  # {sa['description']}" if sa.get("description") else ""
            lines.append(f"  [{sa['name']}]{desc}")
        lines.append("")

    return "\n".join(lines)


def diagnose_and_repair(current_prompt: str, failed_results: list[dict],
                        client: OpenAI, model: str,
                        uc_config: dict | None = None) -> tuple[str, str]:
    failures_text = ""
    for r in failed_results:
        tc_id = r.get("tc_id", "?")
        failures_text += f"\n--- {tc_id}: {r.get('name','?')} ---\n"
        failures_text += f"Summary: {r.get('summary','')}\n"
        failures_text += "Failed criteria:\n"
        for cr in r.get("results", []):
            if cr.get("verdict") != "PASS":
                failures_text += f"  ✗ {cr['criterion']}\n    Reason: {cr.get('reason','')}\n"
        failures_text += "Transcript excerpt:\n"
        for e in r.get("transcript", [])[-8:]:
            if e.get("type") == "tool_call":
                failures_text += f"  [TOOL] {e['tool']} → {json.dumps(e['result'])[:150]}\n"
            elif e.get("type") == "bot_message":
                failures_text += f"  User: {e['user']}\n  Bot:  {e['bot'][:200]}\n"

    guides_section = f"PLATFORM GUIDES:\n{PROMPT_GUIDES}\n\n" if PROMPT_GUIDES else ""
    ref_names      = _build_reference_names(uc_config) + "\n\n" if uc_config else ""
    prompt = (
        f"{guides_section}"
        f"{ref_names}"
        f"CURRENT PROMPT:\n{current_prompt}\n\n"
        f"FAILED TESTS:\n{failures_text}"
    )

    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    data = json.loads(r.choices[0].message.content)
    return data.get("diagnosis", ""), data.get("new_prompt", current_prompt)


# ── Credential detection ─────────────────────────────────────────────────────

_EMAIL_PATTERNS = re.compile(
    r'\b(email|e-mail|mail\s*id|email\s*address|email\s*id)\b', re.IGNORECASE
)
_PHONE_PATTERNS = re.compile(
    r'\b(phone|mobile|cell|contact\s*number|phone\s*number|mobile\s*number)\b', re.IGNORECASE
)
_OTP_PATTERNS = re.compile(
    r'\b(otp|one[\s-]?time\s*password|verification\s*code|verify.*code)\b', re.IGNORECASE
)


def detect_credential_needs(prompt_text: str) -> dict:
    """Scan an agent prompt for email, phone, and OTP requirements."""
    return {
        "needs_email": bool(_EMAIL_PATTERNS.search(prompt_text)),
        "needs_phone": bool(_PHONE_PATTERNS.search(prompt_text)),
        "needs_otp":   bool(_OTP_PATTERNS.search(prompt_text)),
    }


# ── Playwright test generation & evaluation ───────────────────────────────────

PLAYWRIGHT_GENERATE_SYSTEM = """You are a senior QA engineer generating browser-based test cases for a customer support AI chatbot. The tests will be executed via Playwright against the live production bot — no mocking.

You receive: (1) BUSINESS REQUIREMENTS, (2) the AGENT PROMPT, (3) the TRIGGER for the agent, (4) sub-agents and tools. The AGENT PROMPT is the ground truth for what the bot will do. Everything you assert MUST come from the agent prompt or the requirements — never from generic assumptions about how a chatbot "should" behave.

GROUNDING RULES — read carefully, the test runner is brutal about these:
1. "expected" describes what the agent prompt instructs the bot to do at this turn, paraphrased. If the prompt says "give a short comparison and ask which detail to expand on", that is the expected. Do NOT add invented specifics like "must give exactly two bullets" or "must show quick reply buttons" unless those specifics appear in the agent prompt.
2. "pass_criteria" must be a verbatim or near-verbatim restatement of constraints that are explicit in the agent prompt or requirements. Phrasings to ban unless the prompt uses them: "exactly N", "must show quick replies", "must use buttons", "must include emojis", "must respond in N words", any number not in the prompt.
3. If the agent prompt does not specify the format of the response (bullets vs. paragraph, count of items, presence of UI widgets), do not assert it. Assert only the SEMANTIC behaviour the prompt requires.
4. If you are tempted to add a criterion based on what would be "good chatbot behaviour" — stop. Only add it if it is in the agent prompt.
5. "agent_behavior_expectations" likewise: each entry must be a paraphrase of a rule from the agent prompt or requirements. Cite the rule in the entry itself when possible (e.g., "Per the prompt's 'one question per message' rule, the bot must never ask two questions in a single message.").
6. Any list of specific names (model names, product names, regions, status values) in a criterion MUST appear in the agent prompt verbatim or be derivable from it. Do not write "must recommend [X, Y, Z]" by listing all models you can think of — only list what the prompt explicitly maps to the user's scenario. If the prompt is ambiguous about which subset applies, use a generic criterion ("the bot must recommend models the prompt maps to this use case") instead of inventing a list.
7. Format of UI output (text, carousel cards, buttons, images) must be asserted only when the prompt explicitly requires that format. If the prompt says "show recommendations" without specifying carousel vs. text list, accept either.

REQUIRED CATEGORIES — at minimum one test per category that is applicable to the agent:
- "happy_path"      — golden flow: trigger → all mandatory steps → successful close
- "edge_case"       — boundary conditions: maximum/minimum values, optional fields, unusual but valid inputs
- "invalid_input"   — user provides clearly invalid data (bad format, refusal, irrelevant text) and the bot must reject or re-prompt per the requirements
- "retry_failure"   — repeated invalid input until the escalation/attempt limit is reached, OR a tool/workflow failure path described in the requirements
- "boundary_route"  — input that triggers a hard route to a sibling agent IF the requirements specify such routing. For these tests: pass_criteria must NOT say "bot must show [ROUTE TO: X]" (Yellow.ai routing is SILENT — no marker appears in chat). Instead write: "The bot's response must be consistent with [X Agent] behavior" or "The bot must NOT handle [topic] itself — response should come from [X Agent]".
- "trigger_check"   — verifies the agent activates on its declared trigger phrasing

Per-agent minimums:
- At least 1 happy_path test
- At least 1 edge_case test
- At least 1 invalid_input test
- At least 1 retry_failure OR boundary_route test (whichever the requirements support)
- At least 1 trigger_check test (use the trigger field verbatim or a close paraphrase as turn-1 user message)

CRITICAL RULE — AGENT ACTIVATION BEFORE INVALID INPUT:
Yellow.ai bots route based on user intent. A fresh conversation that starts with random or garbage input ("asdfghjkl", "Blah blah", "xyz") will NOT reach this specific agent — it will get the global bot fallback response ("Hey there, I'm your Royal Enfield virtual assistant..."), which is irrelevant to agent-specific pass criteria.

THEREFORE:
- For "invalid_input" tests: Turn 1 MUST be a valid trigger phrase that activates this agent (use the agent trigger or a close paraphrase). Invalid data is introduced from turn 2 onwards, AFTER the agent is active.
- For "retry_failure" tests: Same rule — turn 1 must trigger the agent; repeated bad inputs go in turns 2+.
- For "edge_case" tests: Turn 1 should activate the agent scope; the edge condition is introduced in the same or subsequent turns.
- NEVER start a test with random/gibberish text as turn 1. The agent must be triggered first.
- The only exception is "trigger_check" tests which intentionally verify the trigger phrasing.

For each test case produce these EXACT fields:
- "test_id":   "PT-001", "PT-002", etc.
- "name":      short label under 70 chars
- "category":  one of the strings above
- "setup_notes": one short sentence describing the precondition / scenario context (under 200 chars). May be empty string if none.
- "turns":     array of {user: "exact customer message", expected: "behavioral description of what the bot SHOULD do/say at this turn"}
- "pass_criteria": array of objective assertions about the complete conversation — what MUST be true in the transcript
- "agent_behavior_expectations": array of agent-level invariants — what the agent must/must-not do across the whole conversation (e.g. "must call getProductDetailsForCancel before any other workflow", "must NOT route to any agent other than this one", "must ask for cancellation reason before offering retention")

Rules:
- EVERY turn MUST have a non-empty "user" string. A turn without a user message is invalid and will break the test runner.
- "turns" should have 2–5 turns. Trigger-check tests may be 1–2 turns.
- "expected" must be behavioral, not an exact quote. Specific enough for an LLM judge to verify from captured text + button labels.
- When the bot shows quick-reply buttons (e.g. "Yes", "Cancel"), the NEXT turn's "user" message should be one of those exact labels — the framework will click the button if a matching one is visible, otherwise type the label.
- The test framework observes ONLY visible bot text and button labels. It CANNOT see memory writes, variable values, or workflow call logs. Write pass_criteria and agent_behavior_expectations that judge ONLY the visible conversation.
- Do NOT rely on specific mock data — this runs against the real bot.
- Return JSON only: {"tests": [...]}

CREDENTIAL AND OTP RULES (only if credentials are provided below):
- When the bot asks for an email address, use the provided VALID email for happy-path tests. For validation edge-case tests, use the INVALID email variations listed below.
- When the bot asks for a phone number, use the provided VALID phone for happy-path tests. For validation edge-case tests, use the INVALID phone variations listed below.
- When the bot asks for an OTP / verification code, use the literal placeholder {{OTP}} as the user message. The test framework will pause automation and let the human enter the real OTP.
- Generate at least one happy-path test that uses valid credentials end-to-end (with {{OTP}} for OTP steps).
- Generate at least one invalid_input test per credential type that tries an invalid variation first, expects an error message, then retries with the valid value."""


def generate_playwright_tests(requirements: str, sub_agents: list, tools: list,
                               prompt: str, client: OpenAI, model: str,
                               email: str = "", phone: str = "",
                               trigger: str = "") -> list[dict]:
    sa_text    = "\n".join(f"- [{sa['name']}]: {sa.get('description','')}" for sa in sub_agents)
    tools_text = "\n".join(f"- {t['name']}: {t.get('description','')}" for t in tools)
    prompt_sec = f"\nAGENT PROMPT:\n{prompt}\n" if prompt and prompt.strip() else ""
    trigger_sec = f"\nAGENT TRIGGER (phrasing that should activate this agent):\n{trigger.strip()}\n" if trigger and trigger.strip() else ""

    cred_section = ""
    if email or phone:
        cred_section = "\nCREDENTIALS FOR TESTING:\n"
        if email:
            local, domain = email.rsplit("@", 1) if "@" in email else (email, "")
            domain_parts = domain.rsplit(".", 1) if "." in domain else (domain, "")
            variations = []
            if domain:
                variations.append(f"{local}@{domain.replace('.', '')}")   # missing dot
                if len(domain_parts) == 2:
                    name, tld = domain_parts
                    if len(name) > 2:
                        variations.append(f"{local}@{name[:-2]}{name[-1]}{name[-2]}.{tld}")  # transposed chars
                variations.append(f"{local}{domain}")                     # missing @
            cred_section += f"  VALID email: {email}\n"
            cred_section += f"  INVALID email variations (use these for validation tests): {', '.join(variations)}\n"
        if phone:
            variations = []
            if len(phone) >= 5:
                variations.append("0" + phone[1:])                        # wrong leading digit
                variations.append(phone[:-1])                             # one digit short
                variations.append(phone + "00")                           # too many digits
            cred_section += f"  VALID phone: {phone}\n"
            cred_section += f"  INVALID phone variations (use these for validation tests): {', '.join(variations)}\n"
        needs = detect_credential_needs(prompt)
        if needs.get("needs_otp"):
            cred_section += "  OTP: Use {{OTP}} as the user message when the bot asks for an OTP.\n"

    user_msg = (
        f"BUSINESS REQUIREMENTS:\n{requirements}\n"
        f"{trigger_sec}"
        f"{prompt_sec}\n"
        f"SUB-AGENTS (routing destinations):\n{sa_text}\n\n"
        f"TOOLS:\n{tools_text}\n\n"
        f"{cred_section}\n"
        "Generate test cases for live Playwright browser testing of this agent. "
        "Cover every required category. Ground every assertion in the requirements + prompt — do not invent behaviour."
    )
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PLAYWRIGHT_GENERATE_SYSTEM},
                  {"role": "user",   "content": user_msg}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    tests = json.loads(r.choices[0].message.content).get("tests", [])

    # Validate: every turn must have a non-empty user message.
    # Drop invalid turns; drop tests that end up with zero valid turns.
    valid_categories = {"happy_path", "edge_case", "invalid_input", "retry_failure",
                        "boundary_route", "trigger_check"}
    cleaned = []
    for t in tests:
        valid_turns = [
            turn for turn in t.get("turns", [])
            if isinstance(turn, dict) and turn.get("user", "").strip()
        ]
        if not valid_turns:
            continue
        t["turns"] = valid_turns
        cat = (t.get("category") or "").strip().lower()
        t["category"] = cat if cat in valid_categories else "happy_path"
        t.setdefault("setup_notes", "")
        if not isinstance(t.get("agent_behavior_expectations"), list):
            t["agent_behavior_expectations"] = []
        cleaned.append(t)
    return cleaned


PLAYWRIGHT_EVAL_SYSTEM = """You are a strict test evaluator for a live customer support chatbot tested via browser automation.

Each conversation turn contains:
- user: what the customer typed
- expected: behavioral description of what the bot SHOULD do/say
- actual: what the bot actually replied (captured text + any button/quick-reply labels from the live browser)

Judge each turn strictly but fairly:
- "expected" is behavioral, not an exact-match string. Do not penalise rephrasing.
- Judge SEMANTIC behaviour, not format. Unless the criterion explicitly requires a specific count, widget, wording, emoji, or structure, ignore those surface features.
- If a pass_criterion says "exactly N bullets" or "must show quick replies" but neither phrase appears in the underlying agent requirements/prompt as a hard rule, treat the criterion as informational and PASS the turn so long as the bot conveyed the required semantic content. When you do this, note "format detail not specified in source — judged semantically" in the reason.
- The "actual" field contains only what was visible in the chat — text messages and button labels. It cannot show internal bot state like memory writes, variable values, or workflow calls. Do NOT fail a turn because the actual text doesn't prove an internal action happened — only judge what the bot SAID or SHOWED.
- Quick-reply button labels appear as newline-separated text (e.g. "Offers\\nEMI Calculator\\nBenefits") in the actual field, or as a "[buttons: A | B | C]" suffix.
- Multi-card carousels appear as a "[carousel cards (N):\\n - card 1 text\\n - card 2 text\\n - ...]" block. Treat every card listed there as something the bot SHOWED. If a criterion says the bot must recommend models A, B, C and the carousel block contains cards mentioning A, B, C — the criterion PASSES, even if the inline text only names the first model.
- A turn marked "(framework: routed outside scope)" or "(ERROR: ...)" must fail that turn with the reason given.

GLOBAL FALLBACK RESPONSES:
If the user's turn-1 input is random garbage, gibberish, or completely off-topic (e.g. "asdfghjkl", "Blah blah", "xyz123") AND the bot responds with a generic brand greeting such as "Hey there, I'm your Royal Enfield virtual assistant, here to help with everything Royal Enfield..." — this is the platform-level global fallback, NOT a failure of the specific agent. In this situation:
- Mark the turn PASS if the criterion only requires the bot to not understand the intent.
- Do NOT fail the turn just because the bot didn't give an agent-specific clarification message — the agent was never triggered.
- If the expected description says the bot must say a very specific agent-scoped message (e.g. "I can help with service overview, booking a service..."), but the actual is a generic brand greeting, mark it as a NOTE (not a hard FAIL) and explain that the agent requires a trigger phrase before it can respond with agent-specific content. Set the overall result for this turn based on whether the core intent-detection requirement was met, not the exact wording.

CROSS-AGENT ROUTING — CRITICAL:
Yellow.ai routes conversations between agents SILENTLY. There is NO visible "[ROUTE TO: ...]" marker in the chat. When the current agent routes to a sibling agent, the very next bot response is produced by the SIBLING agent — it looks like a normal bot reply but comes from a different agent entirely.

This is the most important thing to get right. Follow these rules exactly:

1. IDENTIFY THE SPEAKER: Before judging any turn, check whether the bot's actual response matches the behavior of any KNOWN AGENT listed below. If the response content is consistent with a known sibling agent's purpose (e.g. the Ride Sure Agent providing Ride Sure plan details, the Booking Agent asking for a name), that response came from the sibling agent — NOT from the agent under test.

2. ROUTING IS PROVEN BY CONTENT: A criterion like "bot must route to [X Agent] and stop" is SATISFIED if the actual response content matches what Agent X would say per its prompt. The absence of an explicit "[ROUTE TO: X]" marker does NOT mean routing failed. Yellow.ai never shows that marker to the user.

3. DO NOT DOUBLE-PENALISE: If routing happened (proven by the sibling agent's content appearing), do NOT also fail the criterion "bot must not handle this topic itself" — the bot didn't handle it, the sibling agent did.

4. WHEN IN DOUBT: If a bot response looks like it belongs to a different agent than the one being tested, give benefit of the doubt that routing succeeded. Mark the routing criterion PASS and note "response content consistent with [X Agent] behavior — routing confirmed."

5. If no KNOWN AGENTS context is provided, judge only what is explicitly visible in the transcript.

Then judge each pass criterion AND each agent_behavior_expectation against the full conversation. Both lists are first-class — any FAIL in either fails the overall test.

Respond ONLY with this JSON (no markdown):
{"turn_verdicts": [{"turn": 1, "verdict": "PASS|FAIL", "reason": "one sentence"}], "criteria_results": [{"criterion": "...", "verdict": "PASS|FAIL", "reason": "one sentence"}], "behavior_results": [{"expectation": "...", "verdict": "PASS|FAIL", "reason": "one sentence"}], "overall": "PASS|FAIL", "summary": "one sentence"}
overall = PASS only if ALL turn verdicts AND ALL criteria verdicts AND ALL behavior verdicts are PASS."""


def evaluate_playwright_transcript(turns: list, criteria: list,
                                    client: OpenAI, model: str,
                                    behavior_expectations: list | None = None,
                                    other_agent_context: dict | None = None) -> dict:
    """
    Evaluate a Playwright transcript.

    other_agent_context: dict mapping agent_name -> prompt_text for all agents
    NOT under test. Passed to the evaluator so it can reason correctly about
    cross-agent routing (silent Yellow.ai handoffs).
    """
    if not turns:
        return {"overall": "ERROR", "summary": "No turns captured",
                "turn_verdicts": [], "criteria_results": [], "behavior_results": []}

    behavior_expectations = behavior_expectations or []

    lines = []
    for t in turns:
        lines.append(f"Turn {t.get('turn', '?')}:")
        lines.append(f"  User:     {t.get('user', '')}")
        lines.append(f"  Expected: {t.get('expected', '')}")
        lines.append(f"  Actual:   {t.get('actual', '(no response captured)')}")

    criteria_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria)) or "(none)"
    behavior_text = "\n".join(f"{i+1}. {b}" for i, b in enumerate(behavior_expectations)) or "(none)"

    # Build cross-agent context block so the evaluator knows what sibling agents do.
    # Placed BEFORE the conversation so the evaluator reads agent context first.
    if other_agent_context:
        agent_blocks = []
        for name, prompt_text in other_agent_context.items():
            # Truncate very long prompts to keep eval token cost reasonable
            truncated = prompt_text[:1800] + "\n...(truncated)" if len(prompt_text) > 1800 else prompt_text
            agent_blocks.append(f"### {name}\n{truncated}")
        agents_section = (
            "KNOWN SIBLING AGENTS — read these FIRST before judging any turn.\n"
            "If a bot response matches one of these agents' behavior, routing succeeded.\n\n"
            + "\n\n---\n\n".join(agent_blocks)
            + "\n\n"
        )
    else:
        agents_section = ""

    prompt = (
        f"{agents_section}"
        f"CONVERSATION:\n{chr(10).join(lines)}\n\n"
        f"PASS CRITERIA:\n{criteria_text}\n\n"
        f"AGENT BEHAVIOR EXPECTATIONS:\n{behavior_text}"
    )

    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": PLAYWRIGHT_EVAL_SYSTEM},
                      {"role": "user",   "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        out = json.loads(r.choices[0].message.content)
        out.setdefault("behavior_results", [])
        return out
    except Exception as e:
        return {"overall": "ERROR", "summary": str(e),
                "turn_verdicts": [], "criteria_results": [], "behavior_results": []}


def compute_diff(old: str, new: str) -> list[dict]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    result = []
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            result.append({"type": "header", "text": line})
        elif line.startswith("+"):
            result.append({"type": "add", "text": line[1:]})
        elif line.startswith("-"):
            result.append({"type": "remove", "text": line[1:]})
        else:
            result.append({"type": "context", "text": line[1:] if line.startswith(" ") else line})
    return result


# ── Optimization loop ─────────────────────────────────────────────────────────

def run_optimization(run_id: int, uc_id: int):
    eq = _run_queues.get(run_id)
    if not eq:
        return

    try:
        client, model = _make_client()
        uc    = db.get_use_case(uc_id)
        tests = db.get_tests(uc_id)
        run   = db.get_run(run_id)
        mode  = run["mode"]
        max_n = run["max_iterations"]

        openai_tools = build_openai_tools(uc["tools"])
        current_prompt = uc["prompt"]["content"]

        eq.put({"type": "run_start", "run_id": run_id, "total_tests": len(tests), "max_iterations": max_n})

        passed = 0
        n      = 0
        for n in range(1, max_n + 1):
            if _should_stop(run_id):
                break

            eq.put({"type": "iteration_start", "run_id": run_id, "n": n})
            db.update_run(run_id, current_iteration=n)

            system_prompt = build_system_prompt(current_prompt)
            iter_results  = []
            passed        = 0

            for tc in tests:
                if _should_stop(run_id):
                    break
                tc_id = tc["test_id"]
                eq.put({"type": "test_start", "tc_id": tc_id, "name": tc["name"], "run_id": run_id})

                result = run_conversation(
                    tc_id, tc["conversation_script"], tc["mock_overrides"],
                    system_prompt, openai_tools, client, model, eq, run_id=run_id
                )
                if _should_stop(run_id):
                    break
                eq.put({"type": "eval_start", "tc_id": tc_id})
                ev = evaluate_transcript(result, tc["pass_criteria"], client, model)

                overall = ev.get("overall", "ERROR")
                if overall == "PASS":
                    passed += 1

                test_result = {
                    "tc_id": tc_id, "name": tc["name"],
                    "overall": overall, "summary": ev.get("summary", ""),
                    "results": ev.get("results", []),
                    "transcript": result["transcript"],
                    "tool_calls_made": result.get("tool_calls_made", {}),
                }
                iter_results.append(test_result)

                eq.put({"type": "test_complete", **test_result})

            total = len(tests)
            db.update_run(run_id, current_pass=passed)

            diagnosis  = ""
            new_prompt = current_prompt

            if passed < total and not _should_stop(run_id):
                eq.put({"type": "diagnosing", "run_id": run_id, "n": n})
                failed = [r for r in iter_results if r["overall"] != "PASS"]
                diagnosis, new_prompt = diagnose_and_repair(current_prompt, failed, client, model, uc)
                diff = compute_diff(current_prompt, new_prompt)
            else:
                diff = []

            db.save_iteration(run_id, n, current_prompt, iter_results, passed, total, diagnosis, new_prompt)

            iter_event = {
                "type": "iteration_complete",
                "run_id": run_id, "n": n,
                "passed": passed, "total": total,
                "diagnosis": diagnosis,
                "diff": diff,
                "all_pass": passed == total,
            }
            eq.put(iter_event)

            if passed == total:
                db.save_prompt(uc_id, new_prompt, create_version=True, label="optimized")
                db.update_run(run_id, status="done", ended_at=datetime.now().isoformat(), current_pass=passed)
                eq.put({"type": "done", "run_id": run_id, "passed": passed, "total": total,
                        "iterations": n, "final_prompt": current_prompt})
                return

            if mode == "step" and not _should_stop(run_id):
                db.update_run(run_id, status="paused")
                eq.put({"type": "paused", "run_id": run_id, "n": n})
                ev_obj = _step_events.get(run_id)
                if ev_obj:
                    ev_obj.clear()
                    ev_obj.wait(timeout=3600)
                db.update_run(run_id, status="running")

            if _should_stop(run_id):
                break

            # Apply repaired prompt for next iteration
            if new_prompt != current_prompt:
                db.save_prompt(uc_id, new_prompt, create_version=True, label="optimized")
                current_prompt = new_prompt

        # Exhausted iterations or stopped
        final_status = "stopped" if _should_stop(run_id) else "done"
        db.update_run(run_id, status=final_status, ended_at=datetime.now().isoformat())
        eq.put({"type": "done", "run_id": run_id, "passed": passed,
                "total": len(tests), "iterations": n, "final_prompt": current_prompt})

    except Exception as e:
        if eq:
            eq.put({"type": "error", "message": str(e)})
        db.update_run(run_id, status="error", ended_at=datetime.now().isoformat())
        import traceback; traceback.print_exc()
    finally:
        _stop_flags.discard(run_id)
