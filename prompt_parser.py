"""
prompt_parser.py — Detects variables, tool calls, and agent transfers
in Yellow.ai V3 agent prompts using guide-defined syntax patterns.

Syntax patterns detected (per agent-prompt-guide.md):
  {{variable_name}}          — V3 standard variable reference
  [camelCaseWord]            — Variable reference in message examples (sample style)
  [Multi Word Agent Name]    — Agent transfer (routing)
  [kebab-case-tool]          — Tool call (guide style)
  [kb: topic]                — Knowledge base lookup
  @[workflow:slug]           — Explicit workflow call with slug
  Call ToolName / @ToolName  — Bare tool call references in instructions
"""

import re

# ── Regex patterns ─────────────────────────────────────────────────────────────

# {{variable_name}} — double brace V3 variable
_RE_VAR_BRACE = re.compile(r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}')

# @[workflow:slug] — explicit workflow with slug
_RE_WORKFLOW_EXPLICIT = re.compile(r'@\[workflow:([a-zA-Z0-9_]+)\]')

# [content] — bracket actions; captures everything inside
_RE_BRACKET = re.compile(r'\[([^\]]+)\]')

# "Call ToolName" — bare tool call in instruction text (any casing)
_RE_CALL_BARE = re.compile(r'\bCall\s+([a-zA-Z][a-zA-Z0-9]{3,})\b')

# @ToolName — bare @-prefixed tool reference (not @[workflow:slug])
_RE_AT_BARE = re.compile(r'@([a-zA-Z][a-zA-Z0-9]{3,})\b')

# Single camelCase/lowercase word (no spaces, no hyphens) — variable in messages
_RE_SINGLE_WORD = re.compile(r'^[a-zA-Z][a-zA-Z0-9]*$')

# kebab-case — guide-style tool slug
_RE_KEBAB = re.compile(r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _classify_bracket(text: str) -> str:
    """Classify content inside [...] as 'variable', 'agent', 'tool', or 'kb'."""
    t = text.strip()
    if t.lower().startswith('kb:'):
        return 'kb'
    if ' ' in t:
        return 'agent'          # Multi-word → agent transfer
    if _RE_SINGLE_WORD.match(t):
        return 'variable'       # Single camelCase/lowercase word → variable ref in message
    if _RE_KEBAB.match(t):
        return 'tool'           # kebab-case → tool slug
    return 'tool'               # Anything else → treat as tool


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_prompt(
    prompt: str,
    configured_tools: list[dict],
    configured_sub_agents: list[dict],
    configured_memory_keys: list[dict],
) -> dict:
    """
    Parse a prompt and return detected variables, tools, agents, and KB lookups
    matched against the configured items for this use case.

    Returns:
      {
        variables: [{name, source, matched, description?}],
        tools:     [{name, source, matched, description?}],
        agents:    [{name, source, matched, description?}],
        kb_lookups: [str],
        summary:   {variables: {total, unmatched}, tools: ..., agents: ...}
      }
    """
    tool_map   = {t['name'].lower(): t for t in configured_tools}
    agent_map  = {a['name'].lower(): a for a in configured_sub_agents}
    memkey_map = {m['key_name'].lower(): m for m in configured_memory_keys}

    variables: dict[str, dict] = {}
    tools:     dict[str, dict] = {}
    agents:    dict[str, dict] = {}
    kb_lookups: list[str]      = []

    def _add_variable(name: str, source: str):
        if name not in variables:
            cfg = memkey_map.get(name.lower())
            variables[name] = {
                'name':        name,
                'source':      source,
                'matched':     cfg is not None,
                'description': cfg['description'] if cfg else '',
            }

    def _add_tool(name: str, source: str):
        if name not in tools:
            cfg = tool_map.get(name.lower())
            tools[name] = {
                'name':        name,
                'source':      source,
                'matched':     cfg is not None,
                'description': cfg['description'] if cfg else '',
            }

    def _add_agent(name: str, source: str):
        if name not in agents:
            cfg = agent_map.get(name.lower())
            agents[name] = {
                'name':        name,
                'source':      source,
                'matched':     cfg is not None,
                'description': cfg['description'] if cfg else '',
            }

    # 1. {{variable_name}}
    for m in _RE_VAR_BRACE.finditer(prompt):
        _add_variable(m.group(1), '{{variable}}')

    # 2. @[workflow:slug] — remove from prompt before bracket scan
    cleaned = prompt
    for m in _RE_WORKFLOW_EXPLICIT.finditer(prompt):
        slug = m.group(1)
        _add_tool(slug, '@[workflow:slug]')
        cleaned = cleaned.replace(m.group(0), '')  # prevent double-match

    # 3. [bracket] content
    for m in _RE_BRACKET.finditer(cleaned):
        text = m.group(1)
        kind = _classify_bracket(text)
        name = text.strip()
        if kind == 'kb':
            topic = name[3:].strip()  # strip "kb:"
            if topic not in kb_lookups:
                kb_lookups.append(topic)
        elif kind == 'variable':
            _add_variable(name, '[camelCase]')
        elif kind == 'agent':
            _add_agent(name, '[Agent Name]')
        else:
            _add_tool(name, '[tool-slug]')

    # 4. "Call ToolName" bare references
    for m in _RE_CALL_BARE.finditer(prompt):
        _add_tool(m.group(1), 'Call X')

    # 5. @ToolName bare references (not @[workflow:...])
    for m in _RE_AT_BARE.finditer(prompt):
        name = m.group(1)
        # Skip if already captured as part of @[workflow:slug]
        pos = m.start()
        if prompt[pos:pos+2] != '@[':
            _add_tool(name, '@ToolName')

    def _summary(d: dict) -> dict:
        total     = len(d)
        unmatched = sum(1 for v in d.values() if not v['matched'])
        return {'total': total, 'unmatched': unmatched}

    return {
        'variables':  sorted(variables.values(), key=lambda x: (x['matched'], x['name'])),
        'tools':      sorted(tools.values(),     key=lambda x: (x['matched'], x['name'])),
        'agents':     sorted(agents.values(),    key=lambda x: (x['matched'], x['name'])),
        'kb_lookups': kb_lookups,
        'summary': {
            'variables': _summary(variables),
            'tools':     _summary(tools),
            'agents':    _summary(agents),
        },
    }
