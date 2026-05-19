# Prompt Optimization Lab — Complete Architecture & Knowledge Transfer Guide

**Audience:** Engineers onboarding to this system. This document covers why it was built, every design decision, how every component works, and how to extend it.

---

## Table of Contents

1. [Why This Exists — The Problem](#1-why-this-exists--the-problem)
2. [What This System Does](#2-what-this-system-does)
3. [Yellow.ai Platform Context — Required Reading](#3-yellowai-platform-context--required-reading)
4. [System Architecture Overview](#4-system-architecture-overview)
5. [File Map](#5-file-map)
6. [Database Layer — db.py](#6-database-layer--dbpy)
7. [Core Runner — runner.py](#7-core-runner--runnerpy)
8. [Prompt Parser — prompt_parser.py](#8-prompt-parser--prompt_parserpy)
9. [Web API — web_app.py](#9-web-api--web_apppy)
10. [Frontend — templates/index.html](#10-frontend--templatesindexhtml)
11. [Configuration — lab_config.json](#11-configuration--lab_configjson)
12. [Platform Reference Files](#12-platform-reference-files)
13. [The Optimization Loop — End-to-End Flow](#13-the-optimization-loop--end-to-end-flow)
14. [Real-Time Streaming — SSE Event Reference](#14-real-time-streaming--sse-event-reference)
15. [Data Flows — Request Lifecycle](#15-data-flows--request-lifecycle)
16. [Key Design Decisions & Tradeoffs](#16-key-design-decisions--tradeoffs)
17. [Running the System](#17-running-the-system)
18. [Adding a New Use Case](#18-adding-a-new-use-case)
19. [Common Failure Modes](#19-common-failure-modes)

---

## 1. Why This Exists — The Problem

### The Yellow.ai Prompt Problem

Yellow.ai customer support bots are driven by **frontend agent prompts** — plain-text documents that tell the bot what to do step by step (check authentication, call tool X, if result = Y then route to agent Z). These prompts are complex — the DAZN cancellation agent, for example, has 8 steps, 6 tool calls, 5 routing destinations, and dozens of conditional branches.

Writing a correct frontend prompt is hard:
- A missing instruction causes the bot to skip a mandatory step silently
- A wrong tool name causes a silent call failure
- A poorly worded condition causes wrong branching
- There is no compiler, no type checker, no linter — just the bot's runtime behavior

### The Old Workflow

Before this tool:
1. PM/engineer writes a prompt manually
2. Tester opens the live Yellow.ai chat widget
3. Tester types test messages one at a time
4. Tester visually inspects responses
5. Engineer edits the prompt
6. Repeat from step 2

This is **slow** (15–30 min per iteration), **inconsistent** (humans mis-judge pass/fail), and **unscalable** (adding a test case means more manual work). A single prompt might need 10–15 rounds before it's correct.

### What We Wanted

An automated loop that:
1. Reads the business requirements
2. Generates test cases from them
3. Runs a local simulation of the bot
4. Evaluates pass/fail objectively against defined criteria
5. Diagnoses failures and rewrites the prompt
6. Repeats until all tests pass

The engineer provides requirements and configuration. The system does everything else.

---

## 2. What This System Does

The Prompt Optimization Lab is a **local web application** (Flask + SQLite + vanilla JS) that implements an LLM-driven prompt optimization loop.

**Core loop:**

```
Requirements → Generate Tests → Write Prompt → Run Tests (simulation) → Evaluate → Diagnose → Repair → Repeat
```

At each iteration:
- The current prompt is used as the system prompt for GPT
- Each test's conversation script is replayed turn by turn
- Tool calls are intercepted and answered with pre-configured mock responses
- A second LLM call judges each criterion as PASS or FAIL
- If failures exist, a third LLM call diagnoses root causes and rewrites the prompt
- The repaired prompt becomes the input for the next iteration
- This repeats until 100% pass rate or the max iteration limit is reached

**All of this runs locally** — no Yellow.ai API calls, no live bot needed. The simulation runs entirely through the OpenAI API using the same system prompt architecture as the production Yellow.ai V3 bot.

---

## 3. Yellow.ai Platform Context — Required Reading

Understanding the platform is essential to understanding why the system is designed the way it is.

### Two-Layer Prompt Architecture

Yellow.ai V3 bots use **two separate prompt layers** that are concatenated and sent to GPT as a single system message:

```
[ backend_prompt.md ] + [ frontend_prompt.md ]
         ↓                        ↓
   Platform rules          Business logic
   (how to behave)         (what to do)
```

**Backend prompt (master prompt):** Platform-level instructions that apply to all agents. This covers:
- How to execute tool calls (silently, in sequence)
- How to handle memory variables
- How to perform agent routing (say the escalation message, then write `[ROUTE TO: AgentName]`)
- Confidentiality rules (never reveal step numbers, tool names, internal flags)

This file is `backend_prompt.md` and is **read-only** — it is never modified by the optimization loop. It is managed at the platform level.

**Frontend prompt (agent prompt):** The use-case-specific business logic. This is what gets optimized. It describes:
- Step-by-step instructions for what the agent should do
- Which tools to call and when
- Which conditions to check on tool results
- Where to route the customer and under what conditions
- What exact language to use in certain situations

The optimization loop only ever modifies the frontend prompt. The backend prompt is a fixed constraint.

### How the Simulation Replicates Production

In production, Yellow.ai sends `backend_prompt + frontend_prompt` to GPT and intercepts tool calls to execute real workflows. In our simulation:
- `build_system_prompt()` concatenates the same two layers into the OpenAI system message
- Tool calls are intercepted by parsing the OpenAI API response's `tool_calls` field
- Real tool responses are replaced by **mock responses** (JSON objects matching the real return schema)
- Agent routing is detected by looking for `[ROUTE TO: AgentName]` in bot messages

This means the simulation tests the **prompt logic**, not the tool implementations. If a tool returns `productStatus: "ActiveGrace"`, the mock returns exactly that, and we verify the bot correctly routes to `[Customer missed a payment]`.

### Tool Call Syntax in Prompts

Per the Yellow.ai V3 guide (`agent-prompt-guide.md`), tools are referenced in prompts using several patterns:
- `[tool-slug]` — calls a tool (guide canonical form)
- `Call ToolName` — bare reference in instruction text (common in practice)
- `@[workflow:slug]` — explicit workflow reference with slug
- `@ToolName` — bare @-prefixed reference

Variables (memory keys) are referenced as:
- `{{variable_name}}` — V3 standard form
- `[camelCaseWord]` — appears in message templates in practice

Agent transfers use:
- `[Multi Word Agent Name]` — bracket with spaces → routing

---

## 4. System Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Browser (index.html)                     │
│  Sidebar Tabs: Tests | Reqs | Variables | Tools | Prompt | Runs │
│  SSE stream reader → live progress updates                   │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼──────────────────────────────────┐
│                    Flask (web_app.py)                         │
│  REST API routes + SSE endpoint                              │
│  Loads config from lab_config.json                           │
│  Calls db.py for persistence                                 │
│  Calls runner.py for test/optimize operations                │
│  Calls prompt_parser.py for prompt analysis                  │
└──────┬──────────────────────┬───────────────────────────────┘
       │                      │
┌──────▼──────┐        ┌──────▼──────────────────────────────┐
│   db.py     │        │              runner.py               │
│  SQLite     │        │  Optimization loop (background thread)│
│  lab.db     │        │  - build_system_prompt()             │
│  9 tables   │        │  - run_conversation()                │
└─────────────┘        │  - evaluate_transcript()             │
                       │  - generate_tests()                  │
                       │  - diagnose_and_repair()             │
                       │  - compute_diff()                    │
                       │  - run_optimization()                │
                       └──────────────┬──────────────────────┘
                                      │
                       ┌──────────────▼──────────────────────┐
                       │          OpenAI API (GPT-4.1)        │
                       │  Used for 4 distinct purposes:       │
                       │  1. Bot simulation (chat.completions)│
                       │  2. Evaluation (json_object mode)    │
                       │  3. Test generation (json_object)    │
                       │  4. Prompt repair (json_object)      │
                       └─────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     File System                             │
│  backend_prompt.md    — Platform system prompt (fixed)      │
│  lab_config.json      — API keys, model, bot credentials    │
│  lab.db               — SQLite database                     │
│  Prompt Guides/       — 4 Yellow.ai reference files (fixed) │
└─────────────────────────────────────────────────────────────┘
```

### Threading Model

The Flask app is multi-threaded (`threaded=True`). Each optimization or run spawns a background daemon thread. Communication between the background thread and the SSE endpoint (which serves the browser) happens through a per-run `queue.Queue`. Step-through pause/resume uses `threading.Event` per run.

```
Main thread (Flask request handler)
  → creates run in DB
  → registers queue and Event
  → starts daemon thread
  → returns {run_id} immediately to browser

Daemon thread (runner.run_optimization)
  → runs full optimization loop
  → puts events into queue

SSE endpoint thread (per browser connection)
  → reads from queue (blocking, 90s timeout)
  → yields SSE events to browser
```

---

## 5. File Map

| File | Role | Owned by |
|---|---|---|
| `web_app.py` | Flask server, all REST + SSE routes | Backend |
| `runner.py` | Test runner, evaluator, test generator, optimization loop | Backend |
| `db.py` | SQLite schema, all database operations | Backend |
| `prompt_parser.py` | Regex-based prompt syntax extractor | Backend |
| `templates/index.html` | Single-page frontend (all HTML + CSS + JS) | Frontend |
| `lab_config.json` | Runtime config: API keys, model, bot credentials | Config |
| `backend_prompt.md` | Yellow.ai platform system prompt (never optimized) | Platform |
| `Prompt Guides/*.md` | 4 Yellow.ai reference files loaded into repair LLM | Platform |
| `lab.db` | SQLite database (auto-created) | Generated |
| `requirements.md` | Seed data: DAZN business requirements | Seed |
| `frontend_prompt_v1.md` | Seed data: initial DAZN prompt | Seed |
| `tests.json` | Seed data: initial DAZN test cases | Seed |

Files that are **never modified at runtime:** `backend_prompt.md`, all files in `Prompt Guides/`, seed files.

Files that are **read/written at runtime:** `lab_config.json`, `lab.db`.

---

## 6. Database Layer — db.py

### Why SQLite

SQLite was chosen over flat files because the data is inherently relational (a use case has many tests, many runs, many prompt versions) and because it gives us:
- Atomic writes (no partial writes if the server crashes)
- Foreign key cascades (delete a use case → all related tests, runs, iterations delete too)
- WAL mode (readers don't block writers — important for concurrent SSE reads during optimization)
- No external process or setup required

### Schema — 9 Tables

```sql
use_cases           — top-level entity: name, timestamps
  └── requirements  — one-to-one: the business requirements text
  └── prompts       — one-to-many: versioned prompt history
  └── sub_agents    — one-to-many: routing destinations (agent names + descriptions)
  └── memory_keys   — one-to-many: known variables (key_name + descriptions)
  └── tools         — one-to-many: workflow tool definitions (name, description, return_schema JSON)
  └── tests         — one-to-many: test cases (script, criteria, mocks as JSON)
  └── runs          — one-to-many: optimization or manual run records
       └── iterations — one-to-many: per-iteration snapshot (prompt, results, diagnosis, new_prompt)
```

All child tables have `ON DELETE CASCADE` so deleting a use case cleans up everything.

### The `db()` Context Manager

```python
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row   # rows accessible as dicts
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

Every database function opens and closes its own connection. `check_same_thread=False` allows the same DB file to be used from the Flask main thread and runner daemon threads. SQLite in WAL mode is safe for this.

`conn.row_factory = sqlite3.Row` means rows are returned as dict-like objects; every function does `dict(row)` or `[dict(r) for r in rows]` to convert to plain dicts before returning.

### Prompt Versioning

Prompts use an `is_current` flag (0 or 1) rather than a `current_version` field on the use case. This design means:
- There is exactly one row with `is_current = 1` per use case at all times
- Creating a new version: set all rows to `is_current = 0`, then insert new row with `is_current = 1`
- The optimization loop calls `save_prompt(uc_id, new_prompt, create_version=True)` at the end of each iteration that changes the prompt. This gives a full history of every version ever generated

```python
def save_prompt(uc_id, content, create_version=False):
    if create_version:
        # Find next version number
        row = conn.execute("SELECT MAX(version) as mv FROM prompts WHERE use_case_id = ?", (uc_id,)).fetchone()
        next_v = (row["mv"] or 0) + 1
        # Clear current flag on all existing versions
        conn.execute("UPDATE prompts SET is_current = 0 WHERE use_case_id = ?", (uc_id,))
        # Insert new current version
        conn.execute("INSERT INTO prompts ... VALUES (?, ?, ?, 1)", (uc_id, next_v, content))
    else:
        # In-place update of current version (for manual edits that don't warrant a new version)
        conn.execute("UPDATE prompts SET content = ? WHERE id = ?", (content, current_id))
```

### `get_use_case()` — The Central Query

This function returns a single dict with all data for a use case: requirements, current prompt, sub_agents, memory_keys, and tools. It's called frequently (before every run, before every parse) and joins 5 tables:

```python
uc["requirements"] = ...   # string
uc["prompt"] = {...}       # {id, version, content, created_at}
uc["sub_agents"] = [...]   # [{id, name, description}]
uc["memory_keys"] = [...]  # [{id, key_name, description}]
uc["tools"] = [...]        # [{id, name, description, return_schema: dict}]
```

`return_schema` is stored as JSON string in SQLite and deserialized to a dict by `get_use_case()`.

### Seeding

On first boot (`init_db(seed_dir=...)`), if the `use_cases` table is empty, `_seed()` is called. It reads three files from the project directory:
- `requirements.md` → requirements table
- `frontend_prompt_v1.md` → prompts table (version 1)
- `tests.json` → tests table

And pre-populates the DAZN use case's tools, sub_agents, and memory_keys from the hardcoded `_DAZN_TOOLS`, `_DAZN_SUB_AGENTS`, `_DAZN_MEMORY_KEYS` lists in `db.py`. These serve as the seed/reference data for DAZN Monthly Flex Cancellation.

---

## 7. Core Runner — runner.py

This is the most complex file. It has five distinct responsibilities:

### 7.1 Run Registry (In-Memory)

```python
_run_queues:  dict[int, queue.Queue]    # run_id → SSE event queue
_step_events: dict[int, threading.Event] # run_id → pause/resume event
_stop_flags:  set[int]                  # run_ids that should stop
```

These are module-level dicts that persist for the server's lifetime. When a run is created:
1. `register_run(run_id)` creates a Queue and a set Event (starts in "go" state)
2. The background thread puts events into the Queue
3. The SSE endpoint reads from the Queue and streams to the browser
4. `signal_continue(run_id)` sets the Event (wakes the background thread from pause)
5. `signal_stop(run_id)` adds to stop set and sets the Event (wakes thread so it can check the stop flag)

### 7.2 Prompt Guides (Loaded Once at Startup)

```python
PROMPT_GUIDES = _load_guides()
```

At module import time, all 4 Yellow.ai platform guide files are read from `Prompt Guides/` and concatenated into a single string with section separators. This string is injected into the repair LLM's context at every optimization iteration. Loading once at startup avoids repeated disk reads.

### 7.3 `build_system_prompt(frontend_prompt)` — The Simulation Core

```python
def build_system_prompt(frontend_prompt: str) -> str:
    backend = (BASE_DIR / "backend_prompt.md").read_text().strip()
    return backend + "\n\n---\n\n## AGENT INSTRUCTIONS (FRONTEND PROMPT)\n\n" + frontend_prompt
```

This replicates exactly what Yellow.ai does in production: concatenate the backend (platform) prompt with the frontend (agent) prompt and use the result as the GPT system message. The separator `---` and heading `## AGENT INSTRUCTIONS` are formatting conventions that help GPT parse the two layers.

### 7.4 `run_conversation()` — The Bot Simulator

This is the most critical function. It replays a test case's conversation script against GPT:

```python
def run_conversation(tc_id, script, mock_overrides, system_prompt, openai_tools, client, model, eq):
    messages = [{"role": "system", "content": system_prompt}]
    
    for turn_idx, user_msg in enumerate(script):  # one iteration per customer message
        messages.append({"role": "user", "content": user_msg})
        
        for _ in range(15):  # inner loop: handle tool call chains
            resp = client.chat.completions.create(
                model=model, messages=messages,
                tools=openai_tools, tool_choice="auto", temperature=0
            )
            msg = resp.choices[0].message
            
            if msg.tool_calls:
                # Bot wants to call a tool
                for tc in msg.tool_calls:
                    mock = resolve_mock(tc.function.name, mock_overrides)
                    messages.append({"role": "tool", "content": json.dumps(mock)})
                # Loop again — bot will read the mock result and continue
            else:
                # Bot produced a text response
                bot_text = msg.content
                if "[ROUTE TO:" in bot_text:
                    return  # routing detected — test complete
                break  # move to next user turn
```

**Key design choices:**
- `temperature=0` — forces deterministic responses during testing
- Inner loop up to 15 iterations — handles sequences of multiple tool calls before the bot speaks
- Tool calls are **intercepted and mocked** — real workflows never run
- `[ROUTE TO: AgentName]` in the bot's text signals agent routing (per `backend_prompt.md`'s routing rules)
- The entire conversation history (`messages` list) grows with each turn, so GPT has full context

**Mock resolution:** `resolve_mock(tool_name, mock_overrides)` looks up `mock_overrides[tool_name]`. If not found, returns a generic success response. `mock_overrides` is a dict stored per test case in the DB, generated by the LLM during test generation to match the specific scenario being tested.

**OpenAI tool definitions:** `build_openai_tools(tools)` converts the DB tool records into OpenAI function definitions with empty parameter schemas. This is intentional — we don't need GPT to produce correct arguments (we ignore them and return mocks anyway). We only need GPT to *decide* to call the right tool.

### 7.5 `evaluate_transcript()` — The LLM Judge

After each test runs, this sends the transcript to GPT with the pass criteria and asks for a structured verdict:

```python
EVALUATOR_SYSTEM = """You are a strict, objective test evaluator...
Respond ONLY with this JSON:
{"results": [{"criterion": "...", "verdict": "PASS|FAIL", "reason": "..."}], "overall": "PASS|FAIL", "summary": "..."}
overall = PASS only if ALL criteria PASS."""
```

The prompt sent to the evaluator includes:
- The full conversation transcript (user messages, bot replies, tool calls with args and results)
- The total tool call count per tool (to support criteria like "tool X was called exactly once")
- The numbered list of pass criteria

`response_format={"type": "json_object"}` is used to guarantee valid JSON output and prevent GPT from wrapping it in markdown.

The evaluator is intentionally strict: PASS only if clearly and unambiguously satisfied. False positives (wrong PASSes) are worse than false negatives because they would stop the loop prematurely.

### 7.6 `generate_tests()` — LLM Test Generation

When the user clicks "Generate Tests", this function is called with:
- The requirements text (written by the user)
- The configured sub_agents (routing destinations)
- The configured memory_keys (available variables)
- The configured tools with their `return_schema` JSON

It sends these to GPT with `GENERATE_SYSTEM` prompt and gets back a list of test cases. Each test has:
- `test_id`: TC-001, TC-002, etc.
- `name`: short label
- `conversation_script`: list of customer messages only (not bot replies)
- `pass_criteria`: list of objective assertions
- `mock_overrides`: dict of `{tool_name: mock_response}` for this specific scenario

The `mock_overrides` are constrained to the tool's `return_schema` — the LLM is instructed to use only field names and value types from the schema. This is why having accurate return schemas in the Tools tab is important.

### 7.7 `diagnose_and_repair()` — The Prompt Repair LLM

Called after every iteration that has failures. It receives:
1. The full `PROMPT_GUIDES` string (all 4 Yellow.ai platform reference files)
2. The current prompt text
3. For each failing test: the test ID, name, summary, failed criteria with reasons, and the last 8 transcript events

The `REPAIR_SYSTEM` prompt tells GPT to:
- Study the platform guides for correct Yellow.ai V3 syntax
- Diagnose root causes specifically (which instruction is missing/wrong)
- Repair surgically (fix only failing sections)
- Return JSON: `{diagnosis: "...", new_prompt: "..."}`

`temperature=0.3` (slightly above 0) gives the repair LLM a small amount of creativity to try different wordings, while still being mostly deterministic.

### 7.8 `run_optimization()` — The Main Loop

This runs entirely in a background daemon thread. The flow:

```
1. Load use case, tests, run config from DB
2. Emit run_start event
3. For n = 1 to max_iterations:
   a. Emit iteration_start
   b. Build system prompt from current frontend prompt
   c. For each test:
      - run_conversation() → transcript
      - evaluate_transcript() → pass/fail
      - Emit test_complete
   d. If all pass → save final prompt version → emit done → return
   e. If failures:
      - diagnose_and_repair() → diagnosis + new_prompt
      - compute_diff(old, new) → visual diff
      - save_iteration() to DB (captures prompt, results, diagnosis, new_prompt)
      - Emit iteration_complete (with diff)
   f. If mode == "step": pause (Event.clear(), Event.wait(timeout=3600))
      - Browser sends /continue → signal_continue() sets the Event → thread wakes
   g. Apply new_prompt for next iteration
4. If max iterations reached without all passing → emit done with current state
```

**Step-through mode** allows engineers to review each iteration's diagnosis and diff before continuing. The thread blocks on `threading.Event.wait(3600)` — a 1-hour timeout as a safety net. The browser sends `POST /api/runs/<id>/continue` to wake it.

**Iteration snapshots** (`save_iteration()`) capture the complete state at each iteration: the prompt that was tested, all test results, the diagnosis, and the repaired prompt. This gives a full audit trail of what changed and why.

---

## 8. Prompt Parser — prompt_parser.py

### Purpose

When an engineer pastes an existing prompt into the Prompt tab, the Parse button extracts all variables, tool references, and agent transfers from it using regex, then compares against what's configured in the use case.

### Detection Patterns

The parser scans for 5 distinct syntax patterns, in this order:

| Step | Pattern | Example | Classification |
|---|---|---|---|
| 1 | `{{variable_name}}` | `{{termType}}` | Memory key (V3 standard) |
| 2 | `@[workflow:slug]` | `@[workflow:reverseandcancel_xyz]` | Tool call (explicit slug) |
| 3 | `[content]` with spaces | `[Chat With An Agent]` | Agent transfer |
| 3 | `[content]` single word | `[firstName]` | Variable in message |
| 3 | `[content]` kebab-case | `[fetch-account-details]` | Tool call (guide style) |
| 4 | `Call ToolName` | `Call cancelProduct` | Tool call (bare) |
| 5 | `@ToolName` | `@resolveCancellationCase` | Tool call (bare @) |

Step 2 removes matched `@[workflow:slug]` occurrences from the string before step 3 runs, to prevent the bracket regex from double-matching.

### Bracket Classification Logic

```python
def _classify_bracket(text):
    if text.startswith('kb:'):  → 'kb'         # [kb: password reset]
    if ' ' in text:             → 'agent'      # [Chat With An Agent]
    if single_word_regex:       → 'variable'   # [firstName]
    if kebab_regex:             → 'tool'       # [fetch-account-details]
    else:                       → 'tool'       # fallback
```

The key insight: in practice, agent names always contain spaces (`Chat With An Agent`, `Customer missed a payment`), while variable references in messages are always single camelCase words (`firstName`, `cancellableOnDate`). This is a reliable heuristic.

### Match vs. Unmatched

Each detected item is looked up (case-insensitively) in:
- `memory_keys` (by `key_name`) — for variables
- `tools` (by `name`) — for tool references
- `sub_agents` (by `name`) — for agent transfers

Items that don't match anything configured are returned with `"matched": false`. In the UI, unmatched items show with an amber warning and an `+ Add` button. "Apply to Config" saves all unmatched items as new entries in the Variables and Tools tabs.

---

## 9. Web API — web_app.py

### Architecture

Flask app, single file. All routes in one place. The app:
1. Loads config from `lab_config.json` on every relevant request (so config changes take effect without restart)
2. Initializes the DB (`db.init_db(seed_dir=...)`) at module load time
3. Has no session/auth — this is a local developer tool

### Route Reference

```
GET  /                                    → serve index.html

# Config
GET  /api/config                          → return config (API keys masked)
POST /api/config                          → save config

# Use cases
GET  /api/use-cases                       → list all use cases
POST /api/use-cases                       → create new use case
GET  /api/use-cases/<id>                  → get full use case bundle
DELETE /api/use-cases/<id>               → delete use case (cascades all data)

# Sub-components (all PUT — replace entire list atomically)
PUT  /api/use-cases/<id>/requirements     → save requirements text
PUT  /api/use-cases/<id>/variables        → replace sub_agents + memory_keys
PUT  /api/use-cases/<id>/tools            → replace tools list
PUT  /api/use-cases/<id>/prompt           → save/version prompt

# Prompt operations
GET  /api/use-cases/<id>/prompt/versions  → list all prompt versions
GET  /api/use-cases/<id>/prompt/versions/<v> → get specific version content
POST /api/use-cases/<id>/prompt/parse     → parse prompt, return detected items

# Test generation
POST /api/use-cases/<id>/generate-tests   → LLM generates tests from requirements

# Runs
POST /api/use-cases/<id>/run              → start manual test run (no optimization)
POST /api/use-cases/<id>/optimize         → start optimization run
POST /api/runs/<id>/continue              → resume step-through run
POST /api/runs/<id>/stop                  → stop running/paused run
GET  /api/runs/<id>                       → get run record
GET  /api/use-cases/<id>/runs             → list all runs with iteration data

# SSE
GET  /api/stream/<id>                     → SSE event stream for a run
```

### Why PUT (not PATCH) for Sub-Components

Variables, tools, and tests are always replaced atomically. The client sends the entire current list. This avoids diff/merge complexity — there's no partial update, no "add one item" endpoint. The DB uses `DELETE WHERE use_case_id = ?` then re-inserts everything. For the sizes involved (typically < 50 items) this is fast and simple.

### The `_require_key()` Helper

All routes that call the OpenAI API check this first:
```python
def _require_key():
    cfg = load_config()
    key = cfg.get("openai_api_key", "")
    if not key:
        return None
    return key, cfg.get("openai_model", "gpt-4.1")
```
Returns `(api_key, model)` or `None`. If None, the route returns a 400 with an error telling the user to configure the API key.

### Manual Run vs. Optimization Run

**Manual run** (`/api/use-cases/<id>/run`): runs tests once, no repair. Used for "run tests against current prompt and see results". The background thread in `web_app.py` handles this directly (not delegated to `runner.run_optimization`).

**Optimization run** (`/api/use-cases/<id>/optimize`): runs the full multi-iteration loop. Delegates entirely to `runner.run_optimization()` in a background thread.

### SSE Endpoint

```python
@app.route("/api/stream/<int:run_id>")
def api_stream(run_id):
    def generate():
        eq = runner.get_queue(run_id)
        while True:
            try:
                event = eq.get(timeout=90)  # blocks here
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'  # keepalive
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

`stream_with_context` keeps the Flask request context alive across the generator's lifetime. The 90-second timeout + ping keepalive prevents the browser from closing the connection due to inactivity. `X-Accel-Buffering: no` disables Nginx buffering (important if deployed behind a proxy).

---

## 10. Frontend — templates/index.html

A single 1,172-line HTML file. No framework, no build step — vanilla JavaScript + Tailwind CSS (loaded from CDN).

### Layout

```
┌──────────────────────────────────────────────────────┐
│  Header: Use case selector | Mode | Max iter | Buttons│
├──────────────────────┬───────────────────────────────┤
│  Sidebar (300px)     │  Main content area            │
│  Tabs:               │  Test list: per-test cards    │
│  ├── Tests           │  Each card: status, criteria, │
│  ├── Reqs            │  transcript, tool calls       │
│  ├── Variables       │                               │
│  ├── Tools           │                               │
│  ├── Prompt          │                               │
│  └── Runs            │                               │
└──────────────────────┴───────────────────────────────┘
```

### Global State

```javascript
let currentUcId  = null;  // selected use case ID
let currentUc    = null;  // full use case bundle from API
let currentRunId = null;  // active run ID
let currentEs    = null;  // active EventSource (SSE connection)
```

### Key JS Functions

| Function | Purpose |
|---|---|
| `loadUseCase(id)` | Fetches full use case bundle, populates all tabs |
| `showSideTab(name)` | Shows/hides sidebar panels |
| `startRun(endpoint)` | POSTs to /run or /optimize, opens SSE stream |
| `handleSseEvent(ev)` | Main SSE dispatcher — updates UI for each event type |
| `renderTestCard(tc)` | Renders a single test case card in the main area |
| `updateTestCard(ev)` | Updates an existing card with pass/fail result |
| `savePrompt()` | Saves current editor text (in-place, no new version) |
| `parsePrompt()` | Sends prompt to parse endpoint, shows Parse modal |
| `applyParsedItems()` | Adds all unmatched items to Variables/Tools config |
| `renderVariables()` | Rebuilds the Variables tab UI from `currentUc` |
| `renderTools()` | Rebuilds the Tools tab UI from `currentUc` |
| `renderRuns()` | Rebuilds the Runs tab with history and iteration detail |
| `generateTests()` | POSTs to generate-tests, replaces test list |

### SSE Event Handling

The `handleSseEvent(ev)` function handles 12 event types:

| Event type | What triggers it | UI action |
|---|---|---|
| `run_start` | Beginning of any run | Show progress bar, set total count |
| `iteration_start` | New optimization iteration | Update iteration counter |
| `test_start` | Test about to run | Mark card as "running" (spinner) |
| `turn_user` | Customer message in simulation | Append to transcript in card |
| `tool_call` | Tool was called | Append tool call + result to transcript |
| `turn_bot` | Bot replied | Append bot reply to transcript |
| `eval_start` | Evaluation beginning | Update card status to "evaluating" |
| `test_complete` | Test done with verdict | Color card green/red, show criteria results |
| `diagnosing` | Repair LLM running | Update progress text |
| `iteration_complete` | Iteration done | Show pass rate, open step modal if step mode |
| `paused` | Step mode paused | Show Continue/Stop buttons |
| `done` | Run finished | Hide progress, show final summary |
| `error` | Any error | Show error message |
| `ping` | SSE keepalive | Ignored |

### The Step-Through Modal

When mode = "step" and an iteration completes, the `iteration_complete` event triggers `showStepModal(ev)`. The modal shows:
- Iteration number and pass/fail score
- The diagnosis text from the repair LLM
- A colored unified diff of the prompt change (green = added, red = removed, gray = context)

Continue button → `POST /api/runs/<id>/continue` → `runner.signal_continue(run_id)` → background thread wakes from `Event.wait()`.

Stop button → `POST /api/runs/<id>/stop` → `runner.signal_stop(run_id)` → thread wakes, checks `_should_stop`, exits loop.

### The Parse Modal

Triggered by the 🔍 Parse button in the Prompt tab. Structure:
1. Call `POST /api/use-cases/<id>/prompt/parse` with current editor content
2. Render results in three sections: Variables, Tools, Agents
3. Each item: name, detection source tag, green ✓ or amber ! badge
4. Unmatched items: `+ Add` button marks them as "to add" (updates `_parseResult` in memory)
5. "Apply to Config" button: sends all unmatched items to the Variables and Tools APIs, refreshes config

---

## 11. Configuration — lab_config.json

```json
{
  "openai_api_key":    "sk-proj-...",
  "openai_model":      "gpt-4.1",
  "bot_id":            "x1750679463696",
  "yellowai_api_key":  "...",
  "base_url":          "https://nexus.yellow.ai"
}
```

| Field | Used for | Required |
|---|---|---|
| `openai_api_key` | All LLM calls (simulation, evaluation, generation, repair) | Yes |
| `openai_model` | Which model to use for all LLM calls | Yes (defaults to gpt-4.1) |
| `bot_id` | Yellow.ai bot identifier (for future live bot integration) | No |
| `yellowai_api_key` | Yellow.ai API authentication (for future live bot integration) | No |
| `base_url` | Yellow.ai regional endpoint (for future live bot integration) | No |

`bot_id`, `yellowai_api_key`, and `base_url` are stored but not yet used by the optimization loop (which is fully local/simulation-based). They are available for future extension to test against the live bot.

Config is loaded fresh on every relevant request — changing it in the Settings modal takes effect on the next operation without restarting the server.

Sensitive fields are masked in `GET /api/config`: all but the last 4 characters are replaced with `•`. The actual values are never sent to the browser once saved.

---

## 12. Platform Reference Files

All 4 files live in `Prompt Guides/` and are loaded once at startup into `PROMPT_GUIDES`. They are **never written by the system** — they are Yellow.ai's official documentation.

| File | Content | Used by |
|---|---|---|
| `agent-prompt-guide.md` (940 lines) | Variable syntax (`{{var}}`), action syntax (`[tool]`, `[Agent]`, `[kb:]`), step writing rules, data flow | `diagnose_and_repair()` |
| `complex-steps-guide.md` (1082 lines) | Multi-step branching, step anatomy, conditions, routing | `diagnose_and_repair()` |
| `tools-guide.md` (695 lines) | Tool definition, parameter schemas, when to call tools | `diagnose_and_repair()` |
| `v3-engine-guide.md` (481 lines) | V3 platform runtime behavior, what the engine handles automatically | `diagnose_and_repair()` |

These are injected as `PLATFORM GUIDES:` at the top of every repair prompt. The repair LLM reads them before diagnosing and rewriting, so it knows:
- Correct Yellow.ai syntax to use in the repaired prompt
- What the V3 engine handles automatically (so it doesn't add redundant instructions)
- How to structure steps correctly

They are also used by `prompt_parser.py` to define what syntax patterns to detect.

---

## 13. The Optimization Loop — End-to-End Flow

### Setup Phase (User Actions in UI)

```
1. Create use case (or select existing)
2. Vars tab: add sub_agents (routing destinations) + memory_keys (variables)
3. Tools tab: add tools with names, descriptions, return_schema JSON
4. Reqs tab: write business requirements in plain language
5. Generate Tests: LLM reads requirements + tools → produces test cases with mocks
6. Prompt tab: paste initial frontend prompt
7. [Optional] Parse: detect variables/tools in prompt, add missing ones to config
8. Header: choose Auto or Step mode, set max iterations
9. Click ⚡ Optimize
```

### Iteration Phase (Automated)

```
For each iteration n:

  ┌─ Build system prompt ──────────────────────────────────────┐
  │  backend_prompt.md + "---" + current_frontend_prompt       │
  └────────────────────────────────────────────────────────────┘
  
  ┌─ For each test case: ──────────────────────────────────────┐
  │  1. Build OpenAI messages: [system, user_turn_1, ...]      │
  │  2. Call GPT (temperature=0)                               │
  │  3. If tool_call → resolve_mock() → inject tool result     │
  │  4. Repeat until bot sends text message                    │
  │  5. If [ROUTE TO: X] in message → test complete            │
  │  6. Move to next user turn in conversation_script          │
  │  7. After all turns → evaluate_transcript() via LLM judge  │
  └────────────────────────────────────────────────────────────┘
  
  ┌─ If all tests pass: ──────────────────────────────────────┐
  │  Save final prompt as new version                          │
  │  Emit done event                                           │
  │  Return (success)                                          │
  └────────────────────────────────────────────────────────────┘
  
  ┌─ If failures: ─────────────────────────────────────────────┐
  │  diagnose_and_repair(current_prompt, failed_results)       │
  │    → reads PROMPT_GUIDES + current prompt + failures       │
  │    → returns (diagnosis, new_prompt)                       │
  │  compute_diff(current, new) → colored diff for UI         │
  │  save_iteration() → snapshot to DB                         │
  │  If step mode: pause, wait for user Continue/Stop          │
  │  Apply new_prompt as current for next iteration            │
  └────────────────────────────────────────────────────────────┘
```

### Termination Conditions

| Condition | Outcome |
|---|---|
| All tests pass | `status = done`, final prompt saved as new version |
| Max iterations reached | `status = done`, last state preserved, user notified |
| User clicks Stop | `status = stopped`, current iteration may be incomplete |
| Unhandled exception | `status = error`, traceback printed to server console |

---

## 14. Real-Time Streaming — SSE Event Reference

Events flow: `runner.py → queue.Queue → SSE endpoint → browser EventSource → handleSseEvent()`

### Full Event Schema

```javascript
// Run lifecycle
{ type: "run_start",        run_id, total_tests, max_iterations }
{ type: "iteration_start",  run_id, n }
{ type: "done",             run_id, passed, total, iterations, final_prompt }
{ type: "error",            message }
{ type: "ping" }  // keepalive, ignored by UI

// Per-test events
{ type: "test_start",    tc_id, name, run_id }
{ type: "turn_user",     tc_id, turn, message }
{ type: "tool_call",     tc_id, turn, tool, args, result }
{ type: "turn_bot",      tc_id, turn, message }
{ type: "eval_start",    tc_id }
{ type: "test_complete", tc_id, name, overall, summary, results, transcript, tool_calls_made }
  // results: [{criterion, verdict, reason}]

// Optimization-only events
{ type: "diagnosing",          run_id, n }
{ type: "iteration_complete",  run_id, n, passed, total, diagnosis, diff, all_pass }
  // diff: [{type: "add"|"remove"|"context"|"header", text}]
{ type: "paused",  run_id, n }
```

---

## 15. Data Flows — Request Lifecycle

### Generate Tests

```
Browser → POST /api/use-cases/<id>/generate-tests
  web_app.py:
    → _require_key()              # check OpenAI config
    → db.get_use_case(uc_id)      # load requirements, tools, sub_agents, memory_keys
    → runner.generate_tests(...)  # LLM call with GENERATE_SYSTEM
    → normalize test IDs          # ensure TC-001 format
    → db.replace_tests(...)       # atomic replace all tests
    → return {tests, count}
```

### Start Optimization

```
Browser → POST /api/use-cases/<id>/optimize
  web_app.py:
    → db.get_tests(uc_id)
    → db.create_run(uc_id, mode, len(tests), max_iterations)  → run_id
    → runner.register_run(run_id)   # create Queue + Event
    → threading.Thread(target=runner.run_optimization, args=(run_id, uc_id)).start()
    → return {run_id}

Browser → GET /api/stream/<run_id>  (SSE connection)
  web_app.py:
    → runner.get_queue(run_id)
    → yield events from queue until "done"
```

### Step-Through Continue

```
User clicks Continue in step modal
Browser → POST /api/runs/<run_id>/continue
  web_app.py → runner.signal_continue(run_id)
    → _step_events[run_id].set()   # wakes blocked thread
  Background thread: Event.wait() returns
    → db.update_run(run_id, status="running")
    → apply new_prompt
    → start next iteration
```

### Prompt Parse

```
User clicks 🔍 Parse
Browser → POST /api/use-cases/<id>/prompt/parse  {content: "..."}
  web_app.py:
    → db.get_use_case(uc_id)
    → prompt_parser.parse_prompt(content, tools, sub_agents, memory_keys)
    → return {variables, tools, agents, kb_lookups, summary}
Browser: renders parse modal with match status
User: clicks + Add on unmatched items, then Apply to Config
Browser → PUT /api/use-cases/<id>/variables  {sub_agents: [...], memory_keys: [...]}
Browser → PUT /api/use-cases/<id>/tools      {tools: [...]}
Browser → POST .../prompt/parse again        # re-verify all matched
```

---

## 16. Key Design Decisions & Tradeoffs

### Why local simulation instead of live bot API calls?

**Decision:** The optimization loop never calls the Yellow.ai live bot. It simulates the bot locally by running the same two-layer prompt through GPT directly.

**Rationale:**
- Live bot calls are slow (5–15s per turn vs <2s locally)
- Live bot calls require internet, correct credentials, bot to be published
- Live API has rate limits and costs
- Session management (Yellow.ai uses per-session context) adds complexity
- The simulation is *more controllable* — we can mock tool responses exactly as needed per test

**Tradeoff:** The simulation might behave slightly differently from the live bot due to differences in Yellow.ai's internal prompt construction or post-processing. However, since we use the same two-layer prompt, the behavior is very close.

### Why GPT for evaluation instead of deterministic rules?

**Decision:** Test evaluation uses an LLM judge (`evaluate_transcript()`), not hand-coded assertion logic.

**Rationale:**
- Pass criteria are written in natural language ("bot asks for cancellation reason before showing offer")
- Deterministic rules would require parsing bot text, which is fragile and regex-heavy
- LLM judges handle paraphrasing, reordering, and nuance naturally
- The EVALUATOR_SYSTEM prompt is calibrated to be strict: PASS only if "clearly and unambiguously satisfied"

**Tradeoff:** LLM evaluation is slightly non-deterministic (temperature=0 helps but doesn't eliminate it). Occasionally a criterion may get a wrong verdict. This is mitigated by the strict PASS threshold (any FAIL = overall FAIL) and the fact that the loop runs multiple iterations — a false PASS would be caught when the repaired prompt is re-tested.

### Why replace-all for variables/tools instead of per-item CRUD?

**Decision:** `PUT /variables` and `PUT /tools` replace the entire list atomically.

**Rationale:**
- Simplicity: one endpoint, no diff tracking, no "add item" / "remove item" / "update item" endpoints
- Consistency: the UI always sends the complete current state
- For the sizes involved (< 20 tools, < 30 variables typically) the re-insert cost is negligible

**Tradeoff:** Concurrent edits from two browser windows would overwrite each other. Acceptable for a single-user local tool.

### Why threading.Event for step-through pause instead of polling?

**Decision:** The background thread blocks on `threading.Event.wait()` while paused.

**Rationale:**
- Zero CPU usage while paused (no polling loop)
- Instant response when Continue is clicked (Event.set() wakes the thread immediately)
- Clean: the thread's code reads linearly — it doesn't need to check a flag on every line

**Tradeoff:** If the server crashes while paused, the run is lost. On restart, the run shows `status=paused` in DB but the thread and Event are gone. The UI handles this gracefully — the stop button becomes available and the user can manually mark it done.

### Why load PROMPT_GUIDES at startup instead of per-request?

**Decision:** All 4 guide files are read from disk once at module import time.

**Rationale:**
- The guides are 3,198 lines total — reading them 10 times per optimization run (once per iteration) would be wasteful
- The files never change at runtime — they're reference documentation

**Tradeoff:** Changes to the guide files require a server restart. Acceptable since these are rare.

---

## 17. Running the System

### Prerequisites

```bash
pip install flask openai
```

### Start

```bash
cd "prompt optimization/Prompt optimization & measurement"
python web_app.py
# → http://localhost:5001
```

On first boot with an empty `lab.db`, the DAZN Monthly Flex Cancellation use case is seeded automatically from `requirements.md`, `frontend_prompt_v1.md`, and `tests.json`.

### First-time Configuration

1. Open http://localhost:5001
2. Click ⚙ Settings
3. Enter OpenAI API key and select model
4. Save

### Reset the Database

```bash
rm lab.db
python web_app.py   # re-seeds from flat files
```

### Kill a stuck port

```bash
lsof -ti :5001 | xargs kill -9
```

---

## 18. Adding a New Use Case

### Via the UI

1. Click the `+` button next to the use case dropdown
2. Enter a name → creates an empty use case
3. Fill in the tabs in order:
   - **Variables:** add sub_agents (routing destinations) and memory_keys
   - **Tools:** add tool names, descriptions, and return schema JSON
   - **Reqs:** write business requirements
   - Click **Generate Tests** → LLM produces test cases
   - **Prompt:** paste initial frontend prompt
   - Click **🔍 Parse** → verify all tools/variables are matched
4. Click **⚡ Optimize**

### Via Seed Files (for scripted setup)

Add to `db.py`'s `_seed()` or create a new `_seed_myagent()` function with hardcoded tool and agent lists, and point to the appropriate requirements/prompt/tests files.

---

## 19. Common Failure Modes

### "OpenAI API key not configured"
Go to ⚙ Settings and enter the key.

### Server won't start — "address already in use"
```bash
lsof -ti :5001 | xargs kill -9
```

### Optimization runs but all tests fail from iteration 1
Check that:
1. `backend_prompt.md` exists — without it, the system prompt is just the frontend prompt
2. Tool mock responses match the field names the prompt logic expects (e.g., if the prompt checks `productStatus`, the mock must return `productStatus`, not `status`)
3. The conversation script contains only customer messages (not bot replies)

### Tests pass locally but fail in production
The simulation uses mocked tool responses. If the live tool returns different field names or values than what's in `mock_overrides`, behavior diverges. Keep `return_schema` accurate and `mock_overrides` realistic.

### Step-through modal doesn't appear after iteration
Check browser console for SSE connection errors. The SSE stream must be connected before the background thread emits events. If the browser connects after `iteration_complete` was already emitted, the modal won't appear. Reload and start a new run.

### "run not found" in SSE stream
The run's queue no longer exists in memory (server restarted since the run was created). The run record in DB still exists, but it can't be continued. Start a new run.

### Repair LLM produces worse prompt
The repair uses `temperature=0.3` for slight creativity. If it consistently worsens, check:
- That failing test transcripts are meaningful (not all empty)
- That `PROMPT_GUIDES` loaded correctly (check `len(PROMPT_GUIDES) > 0`)
- That pass criteria are specific and objectively verifiable

### Parse modal shows everything as unmatched
The parser matches against what's configured in the Variables and Tools tabs. If those tabs are empty, everything will appear unmatched. Use "Apply to Config" to auto-populate from the prompt, then edit descriptions.
