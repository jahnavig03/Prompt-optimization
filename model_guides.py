"""Model-specific prompt-writing guidelines for optimization.

Each entry describes how to structure and phrase frontend agent prompts so they
perform best when the target LLM is the one listed. Used during repair/optimization.
"""

from __future__ import annotations

# (id, display label, OpenAI API model id or None if guidelines-only)
MODEL_OPTIONS: list[dict] = [
    {"id": "gpt-4.1",           "label": "GPT-4.1",            "api_model": "gpt-4.1",           "provider": "openai"},
    {"id": "gpt-5.1",           "label": "GPT-5.1",            "api_model": "gpt-5.1",           "provider": "openai"},
    {"id": "claude-sonnet-4.6", "label": "Claude Sonnet 4.6",  "api_model": None,                "provider": "anthropic"},
]

_MODEL_BY_ID = {m["id"]: m for m in MODEL_OPTIONS}


def list_models() -> list[dict]:
    allowed = {"gpt-4.1", "gpt-5.1", "claude-sonnet-4.6"}
    return [
        {
            "id": m["id"],
            "label": m["label"],
            "provider": m["provider"],
            "api_available": m["api_model"] is not None,
        }
        for m in MODEL_OPTIONS
        if m["id"] in allowed
    ]


def resolve_api_model(selected: str | None, fallback: str = "gpt-4.1") -> tuple[str, str]:
    """Return (api_model_id, guideline_model_id) for a user selection."""
    fb = fallback or "gpt-4.1"
    if not selected:
        return fb, fb
    entry = _MODEL_BY_ID.get(selected)
    if not entry:
        return selected, selected
    api = entry.get("api_model") or fb
    return api, entry["id"]


def get_model_guidelines(model_id: str) -> str:
    """Return prompt-writing guidelines for the target model."""
    key = (model_id or "").strip()
    if key in _GUIDELINES:
        return _GUIDELINES[key]
    entry = _MODEL_BY_ID.get(key)
    if entry and entry["id"] in _GUIDELINES:
        return _GUIDELINES[entry["id"]]
    return _GUIDELINES["default"]


_PRESENTATION = """
PRESENTATION & MARKDOWN FORMATTING (mandatory for every repaired prompt):
- Output MUST be clean, valid Markdown that renders professionally — even if the input prompt is plain text, wall-of-text, or poorly structured.
- Restructure messy input into a clear document hierarchy. Never return an unstructured blob.
- Heading hierarchy:
  • # — document title / agent name (exactly one at the top)
  • ## — major sections: GOAL, MANDATORY RULES, GLOBAL VALIDATION RULES, WHEN TO USE THIS, etc.
  • ### — step headers: ### Step 1 — Descriptive Name
  • #### — sub-sections within a step (branching, validation sub-rules)
- Use **bold** for critical keywords: **ALWAYS**, **NEVER**, **MANDATORY**, **MUST**, **DO NOT**, **ONLY IF**
- Use bullet lists (-) for rules, conditions, branching paths, and option lists
- Use numbered lists (1. 2. 3.) for sequential actions within a step
- Add a blank line between every section and between list blocks
- Wrap verbatim bot phrases in backticks: `Ask exactly: "How can I help you today?"`
- Use horizontal rules (---) sparingly to separate major document parts
- Do NOT use HTML tags — Markdown only
- Preserve all Yellow.ai syntax exactly: {{variables}}, tool names, [Agent Name] routing, @[workflow:...], @[richMedia:...]
- The final prompt should look like a polished technical specification when rendered in a markdown preview (headings highlighted, bold emphasis visible, lists indented).
"""

_GUIDELINES: dict[str, str] = {
    "default": _PRESENTATION + """
GENERAL LLM PROMPT GUIDELINES:
- Put the most important constraints in the first 20% of the prompt (goal + mandatory rules)
- One action per step — never combine ask + tool call + route in one step
- Use explicit IF / ELSE branching with clear triggers
- Name steps with action verbs: "Collect email", "Validate OTP", "Route to specialist"
- Avoid contradictory instructions; resolve conflicts in favor of MANDATORY RULES
""",

    "gpt-4.1": _PRESENTATION + """
TARGET MODEL: GPT-4.1 (OpenAI)
- GPT-4.1 follows structured Markdown headings reliably — use ## and ### consistently
- Front-load critical rules in a ## MANDATORY RULES section before steps
- Prefer explicit numbered steps over prose paragraphs for multi-turn flows
- Use **bold** labels for decision points: **If user says X:** / **Otherwise:**
- Keep each step under ~15 lines; split long steps into sub-steps with ####
- Repeat non-negotiable constraints once in MANDATORY RULES and once at the relevant step
- Use concrete examples in parentheses for ambiguous user inputs
- Avoid nested conditionals deeper than 2 levels — flatten with bullet sub-lists
""",

    "gpt-4o": _PRESENTATION + """
TARGET MODEL: GPT-4o (OpenAI)
- GPT-4o responds well to role + goal framing at the top under # Agent title
- Use concise bullet rules rather than long paragraphs
- Put tool-call steps in their own ### Step N sections with "Call <toolName>" as the sole action
- Use **CRITICAL:** prefix for rules that must never be violated
- Prefer table-style mappings as bullet lists: "- City commute → Model A, Model B"
- Keep routing instructions explicit: "Route to [Agent Name] when user mentions X"
""",

    "gpt-5.1": _PRESENTATION + """
TARGET MODEL: GPT-5.1 (OpenAI)
- GPT-5.1 handles long context well — still keep steps modular for maintainability
- Use a ## BEFORE YOU START section for preconditions and memory checks
- Structure complex flows as: Goal → Global rules → Step sequence → Edge cases
- Use **Definition (internal):** blocks for terms the model must interpret consistently
- For multi-intent handling, use a decision table in markdown bullets
- Be explicit about what NOT to do — GPT-5.1 benefits from negative constraints
- Minimize redundant repetition; reference earlier sections by name ("per MANDATORY RULES above")
""",

    "o3": _PRESENTATION + """
TARGET MODEL: o3 (OpenAI reasoning)
- o3 excels at explicit logical branching — write IF/THEN as clear bullet trees
- Separate "collect information" steps from "act on information" steps
- Use ## GLOBAL VALIDATION RULES for cross-step constraints (format checks, required fields)
- State invariants explicitly: "Before calling any tool, {{email}} MUST be set"
- Avoid implicit ordering — number every sequential requirement
- For retry/escalation logic, use a dedicated ### Escalation step with attempt counts
""",

    "claude-sonnet-4.6": _PRESENTATION + """
TARGET MODEL: Claude Sonnet 4.6 (Anthropic)
- Claude follows XML-style tags well — wrap major sections in tags when helpful:
  <goal>...</goal> <rules>...</rules> <steps>...</steps>
  (Still use Markdown headings inside and alongside tags for Yellow.ai editor compatibility)
- Put role and tone in the opening paragraph under # title
- Use markdown headings (##, ###) for every section Claude should treat as distinct
- Prefer "You must" / "You must never" phrasing for hard constraints
- Use markdown bullet lists for branching; Claude tracks nested bullets accurately
- Keep tool calls in isolated steps with no user-facing text in the same step
- Add a ## Examples section with 1–2 short good/bad response pairs when disambiguation helps
- Be direct and complete — Claude performs best with fully specified edge cases
""",

    "claude-opus-4.6": _PRESENTATION + """
TARGET MODEL: Claude Opus 4.6 (Anthropic)
- Opus handles nuanced instructions — specify intent behind rules, not just the rule
- Combine Markdown headings with optional XML section tags for major blocks
- Use ## Context and ## Constraints before ## Steps for complex agents
- Document edge-case priority when rules could conflict ("Priority: safety > speed > upsell")
- Longer prompts are acceptable if well-structured with clear headings
- Use **Important:** and **Note:** callouts for exceptions to general rules
- For routing, explain WHY the route happens so Opus generalizes correctly
""",
}
