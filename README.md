# Prompt Optimization Lab

An automated test-and-repair loop for Yellow.ai V3 frontend agent prompts. Paste your business requirements and an initial prompt, configure tools and agents, and the system generates test cases, simulates full conversations, evaluates pass/fail against defined criteria, diagnoses failures, rewrites the prompt, and iterates until every test passes — all without touching the live bot.

---

## Quick Start

### 1. Prerequisites

- Python 3.10+
- An OpenAI API key (GPT-4.1 is the default model)

### 2. Install dependencies

```bash
cd "Prompt optimization & measurement"
pip install flask openai
```

### 3. Start the server

```bash
python web_app.py
```

Open **http://localhost:5001** in your browser.

### 4. Configure your API key

Click **⚙ Settings** (top-right) → paste your OpenAI API key → click **Save**. The key is stored locally in `lab_config.json` and never leaves your machine.

### 5. Create a use case

Click **+ New** next to the use-case dropdown, give it a name (e.g. "Cancellation Agent"), and follow this workflow:

| Tab | What to do |
|---|---|
| **Variables** | Add sub-agents (routing targets) and memory keys your agent uses |
| **Tools** | Add each tool/workflow the agent calls — name, description, return schema |
| **Requirements** | Write plain-English requirements for the agent's behavior |
| **Prompt** | Paste your current frontend prompt (or start with a blank draft) |

### 6. Generate tests

Click **Generate Tests** in the header. The LLM reads your requirements and tools to produce realistic test cases with conversation scripts, pass criteria, and mock tool responses. Review and edit as needed.

### 7. Parse your prompt (optional but recommended)

In the **Prompt** tab, click **🔍 Parse**. The parser scans your prompt for all variable references, tool calls, and agent transfers and checks each one against your configured lists. Unmatched items are flagged — click **Apply to Config** to bulk-add any gaps.

### 8. Run the optimization loop

Open **Run options** (top-right) to configure:

| Setting | Options |
|---|---|
| **Target model** | GPT-4.1, GPT-5.1, or Claude Sonnet 4.6 — prompts are optimized using model-specific writing guidelines |
| **Mode** | **Auto** (runs until 100% pass or max iterations) or **Step** (pause after each iteration to review) |
| **Max iterations** | 1–20 (default 10) |

Click **Optimize**. Watch the Tests panel update in real time via the SSE stream. Test pass/fail counts appear on each test in the sidebar — not in Run options.

Settings (⚙) holds your default OpenAI API key and fallback model. Run options overrides the model for that specific run.

### 9. Copy the final prompt

When all tests show **PASS**, the current prompt in the **Prompt** tab is your optimized result. Copy and paste it into Yellow.ai.

---

## Table of Contents

1. [Why This Exists — The Problem](#1-why-this-exists--the-problem)
2. [What This System Does](#2-what-this-system-does)
3. [Yellow.ai Platform Context](#3-yellowai-platform-context)
4. [System Architecture](#4-system-architecture)
5. [File Structure](#5-file-structure)
6. [File Purposes — Every File Explained](#6-file-purposes--every-file-explained)
7. [Database Schema](#7-database-schema)
8. [API Reference — All Routes](#8-api-reference--all-routes)
9. [The Optimization Loop — Step by Step](#9-the-optimization-loop--step-by-step)
10. [Real-Time Streaming — SSE Events](#10-real-time-streaming--sse-events)
11. [Prompt Parser — Syntax Detection](#11-prompt-parser--syntax-detection)
12. [Frontend — UI Tabs and Operations](#12-frontend--ui-tabs-and-operations)
13. [Configuration](#13-configuration)
14. [Key Design Decisions](#14-key-design-decisions)
15. [Running the System](#15-running-the-system)
16. [How to Add a New Use Case](#16-how-to-add-a-new-use-case)
17. [Common Failure Modes and Fixes](#17-common-failure-modes-and-fixes)

---

## 1. Why This Exists — The Problem

### The Yellow.ai Prompt Problem

Yellow.ai customer support bots are driven by **frontend agent prompts** — plain-text documents that tell the bot exactly what to do, step by step: check if the customer is authenticated, call tool X, if the result equals Y then route to agent Z, otherwise ask for a reason. These prompts are complex. The DAZN cancellation agent, for example, has 8 steps, 6 tool calls, 5 routing destinations, and dozens of conditional branches spread across hundreds of lines.

Writing a correct frontend prompt is hard:
- A missing instruction causes the bot to silently skip a mandatory step
- A wrong tool name causes a silent call failure with no error visible to the user
- An ambiguous condition causes wrong branching — the bot routes to the wrong agent
- There is no compiler, no linter, no static checker — only the bot's live runtime behavior tells you something is wrong

### The Old Manual Workflow

Before this tool existed:
1. PM or engineer writes a prompt manually based on requirements
2. Tester opens the live Yellow.ai chat widget
3. Tester types messages one at a time, following a test script
4. Tester visually reads bot responses and judges pass/fail
5. Engineer edits the prompt
6. Repeat from step 2 until all tests pass

This process takes 15–30 minutes per iteration. It is inconsistent (humans make judgement errors), unscalable (each new test case multiplies the manual effort), and opaque (there is no record of why a prompt changed between versions).

### What We Needed

A system that:
- Reads business requirements written in plain English
- Generates test cases from them automatically, including realistic mock tool responses
- Simulates full multi-turn conversations against GPT (replicating how Yellow.ai sends prompts to its LLM)
- Evaluates every pass criterion objectively using an LLM judge
- Diagnoses exactly which instructions caused each failure
- Rewrites the prompt surgically to fix failures without breaking passing tests
- Repeats this loop automatically until 100% pass rate or a configured iteration limit

The engineer provides the requirements, tools, and an initial prompt. The system handles everything else.

---

## 2. What This System Does

The Prompt Optimization Lab is a **local web application** (Flask + SQLite + vanilla JavaScript) that runs an LLM-driven optimization loop.

**The core loop:**

```
Requirements → Generate Tests → Paste Prompt → Run Tests → Evaluate → Diagnose → Repair → Repeat
```

At each iteration:
1. The current frontend prompt is concatenated with the backend platform prompt to form the GPT system message
2. Each test's conversation script is replayed turn by turn against the OpenAI API
3. Tool calls from GPT are intercepted and answered with per-test mock responses
4. A second LLM call judges each pass criterion as PASS or FAIL from the transcript
5. If any test fails, a third LLM call diagnoses the root cause and rewrites the prompt
6. The repaired prompt becomes the input for the next iteration
7. This repeats until all tests pass or the max iteration limit is reached

**Everything runs locally.** No Yellow.ai API calls, no live bot, no published workflow required. The simulation uses the same two-layer prompt architecture as production, so behavior is representative.

---

## 3. Yellow.ai Platform Context

Understanding the platform is essential to understanding why the system is built the way it is.

### Two-Layer Prompt Architecture

Yellow.ai V3 bots run on a two-layer prompt system. Both layers are concatenated and sent as a single GPT system message:

```
┌─────────────────────────────────────────────┐
│  backend_prompt.md  (platform layer)        │
│  Platform rules: tool call behavior,        │
│  memory rules, routing syntax,              │
│  confidentiality, execution discipline      │
├─────────────────────────────────────────────┤
│  frontend_prompt  (agent layer)             │
│  Business logic: steps, conditions,         │
│  tool calls, routing decisions,             │
│  exact language requirements                │
└─────────────────────────────────────────────┘
              ↓ sent as one system message to GPT
```

**Backend prompt** — `backend_prompt.md` — is platform-managed and never touched by the optimization loop. It defines:
- Tool calls are always silent (no "please wait" messages)
- After a tool call, use only returned values — never guess
- Agent routing: say the escalation message, then write `[ROUTE TO: AgentName]` on a new line
- Routing exceptions (Deceased Customer, country mismatch) happen silently with no preceding text
- Never reveal step numbers, variable names, tool slugs, or internal flags to the customer

**Frontend prompt** — the agent-specific instructions — is what gets optimized. It contains the business logic: what to check, when to call which tool, what conditions determine routing, what exact phrases must be used.

### How the Simulation Matches Production

In production, Yellow.ai sends `backend + frontend` to GPT and intercepts real tool calls to run actual workflows. In this system:
- `runner.build_system_prompt()` concatenates the same two layers
- The OpenAI API's `tool_calls` field is intercepted after each GPT response
- Real tool responses are replaced with **mock responses** — JSON objects matching each tool's declared return schema, customized per test scenario
- Agent routing is detected by scanning for `[ROUTE TO: AgentName]` in bot text

This means the simulation tests **prompt logic correctness**, not tool implementation. The mock responses can be set to any value to test any branch.

### Yellow.ai Prompt Syntax

Per the platform guides, prompts use these syntax patterns:

| Pattern | Example | Meaning |
|---|---|---|
| `{{variable_name}}` | `{{termType}}` | Memory key reference (V3 standard) |
| `[camelCaseWord]` | `[firstName]` | Variable in a message template |
| `[Multi Word Name]` | `[Chat With An Agent]` | Agent transfer / routing |
| `[kebab-case-name]` | `[fetch-account-details]` | Tool call (guide canonical form) |
| `[kb: topic]` | `[kb: refund policy]` | Knowledge base lookup |
| `@[workflow:slug]` | `@[workflow:reverseandcancel_xyz]` | Explicit workflow call with slug |
| `Call ToolName` | `Call cancelProduct` | Bare tool call in instructions |
| `@ToolName` | `@resolveCancellationCase` | Bare @-prefixed tool reference |

---

## 4. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Browser  (index.html)                         │
│  Header: use case selector, mode toggle, iteration count,        │
│          Optimize / Run / Settings buttons                       │
│  Sidebar: Tests | Reqs | Variables | Tools | Prompt | Runs tabs  │
│  Main: live test cards with transcripts and pass/fail results    │
│  Modals: Step-through diff viewer, Parse results, Settings       │
│  EventSource: SSE stream → real-time progress updates            │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP REST + SSE
┌───────────────────────────▼──────────────────────────────────────┐
│                    Flask  (web_app.py)  — port 5001              │
│  30+ REST routes + 1 SSE endpoint                                │
│  Reads lab_config.json for credentials on every request          │
│  Calls db.py for all persistence                                 │
│  Calls runner.py for test/optimize operations                    │
│  Calls prompt_parser.py for prompt analysis                      │
└──────────┬────────────────────────┬─────────────────────────────┘
           │                        │
┌──────────▼──────────┐   ┌─────────▼──────────────────────────────┐
│      db.py          │   │            runner.py                    │
│  SQLite / lab.db    │   │  Background daemon thread per run       │
│  9 tables           │   │                                         │
│  WAL mode           │   │  build_system_prompt()                  │
│  FK cascades        │   │  build_openai_tools()                   │
│  Prompt versioning  │   │  resolve_mock()                         │
│  Iteration history  │   │  run_conversation()      ← bot sim     │
└─────────────────────┘   │  evaluate_transcript()   ← LLM judge   │
                          │  generate_tests()         ← LLM gen    │
                          │  diagnose_and_repair()    ← LLM repair │
                          │  compute_diff()                         │
                          │  run_optimization()       ← main loop  │
                          └──────────────┬──────────────────────────┘
                                         │
                          ┌──────────────▼──────────────────────────┐
                          │        OpenAI API  (GPT-4.1)            │
                          │  4 distinct uses:                       │
                          │  1. Bot simulation  (chat, temp=0)      │
                          │  2. Evaluation      (json_object)       │
                          │  3. Test generation (json_object)       │
                          │  4. Prompt repair   (json_object)       │
                          └─────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                      File System                                 │
│  backend_prompt.md    platform system prompt (never modified)    │
│  Prompt Guides/*.md   4 Yellow.ai reference docs (never mod.)   │
│  lab_config.json      API keys, model, bot credentials          │
│  lab.db               SQLite database (auto-created)            │
└──────────────────────────────────────────────────────────────────┘
```

### Threading Model

Flask runs with `threaded=True`. Each optimization or manual run spawns one background daemon thread. Three in-memory structures (keyed by `run_id`) coordinate between threads:

```
_run_queues:  dict[int, queue.Queue]     events from runner → SSE endpoint → browser
_step_events: dict[int, threading.Event] pause/resume signal for step-through mode
_stop_flags:  set[int]                   run IDs that should stop at next check
```

The SSE endpoint thread reads from the queue with a 90-second blocking timeout; if nothing arrives it sends a `ping` keepalive to prevent browser disconnection.

---

## 5. File Structure

```
Prompt optimization & measurement/
│
├── README.md                    ← this file — full project documentation
├── ARCHITECTURE.md              ← extended engineering deep-dive
├── CLAUDE.md                    ← Claude Code project instructions (AI assistant config)
│
│── Core application ────────────────────────────────────────────────
├── web_app.py                   ← Flask server: all REST API routes + SSE endpoint
├── runner.py                    ← optimization loop, bot simulator, evaluator, test generator
├── model_guides.py              ← per-model prompt-writing guidelines (GPT-4.1, GPT-5.1, Claude, etc.)
├── db.py                        ← SQLite schema definition + all database operations
├── prompt_parser.py             ← regex-based prompt syntax extractor
├── knowledge_store.py           ← acceptance rules + knowledge rubric for evaluation
├── playwright_runner.py         ← live Yellow.ai bot tests via Playwright
├── report_pdf.py                ← PDF report generation for Playwright runs
│
├── templates/
│   ├── index.html               ← main lab UI (Setup → Test & fix → Live validate)
│   └── live_bot.html            ← Live Validate page (Playwright tests against real bot)
│
│── Configuration ───────────────────────────────────────────────────
├── lab_config.json              ← OpenAI key/model + Yellow.ai credentials (gitignore this)
├── lab.db                       ← SQLite database, auto-created on first boot
│
│── Platform files  (read-only — never written by the app) ──────────
├── backend_prompt.md            ← Yellow.ai V3 platform system prompt (backend layer)
├── Prompt Guides/
│   ├── agent-prompt-guide.md    ← 940 lines: variable/action syntax, step writing rules
│   ├── complex-steps-guide.md   ← 1,082 lines: branching, step anatomy, data flow
│   ├── tools-guide.md           ← 695 lines: tool definitions, parameter schemas
│   └── v3-engine-guide.md       ← 481 lines: V3 runtime behavior, auto-handled features
│
│── Seed data  (DAZN Monthly Flex Cancellation example) ─────────────
├── requirements.md              ← plain-language business requirements (seeded into DB)
├── frontend_prompt_v1.md        ← initial DAZN frontend prompt (seeded as version 1)
├── tests.json                   ← initial DAZN test cases (seeded into DB on first boot)
├── tests.template.json          ← blank test case schema template (reference only)
│
│── Legacy / reference files ────────────────────────────────────────
├── master prompt.md             ← original Yellow.ai master prompt reference document
├── instructions.md              ← original manual phase-by-phase optimization guide
├── api-config.md                ← Yellow.ai REST API reference (endpoints, auth, sessions)
├── test_cases.md                ← original hand-written test cases (reference only)
├── bot_config.json              ← Yellow.ai live bot credentials (never commit real keys)
├── bot_config.template.json     ← template for bot_config.json with placeholder values
├── test_runner.py               ← original CLI runner: calls live Yellow.ai bot
├── local_test_runner.py         ← pre-web-app local simulation runner (superseded)
├── requirements.txt             ← pip dependencies (requests for legacy test_runner.py)
│
└── test_results/                ← output from legacy test_runner.py
    ├── TC-001_transcript.json
    ├── TC-002_transcript.json
    └── ...
```

---

## 6. File Purposes — Every File Explained

### `web_app.py`
Flask application entry point. Starts on port 5001. Every HTTP route the browser calls is defined here. Responsibilities:
- Load and save `lab_config.json` for API credentials
- Initialize the database (`db.init_db(seed_dir=...)`) at module load time
- Route all database reads/writes to `db.py`
- Spawn background threads for test runs and optimization via `runner.py`
- Serve the SSE event stream that pushes real-time progress to the browser
- Call `prompt_parser.parse_prompt()` for the Parse endpoint

Config is loaded fresh on every relevant request — changing keys in the Settings modal takes effect immediately without restarting.

### `runner.py`
The engine. Five distinct responsibilities:

**1. Run registry (in-memory)**
Manages a `queue.Queue`, `threading.Event`, and stop flag per active run. The queue carries SSE events from the background thread to the browser. The Event controls step-through pause/resume. These live in memory and are gone if the server restarts.

**2. `build_system_prompt(frontend_prompt)`**
Reads `backend_prompt.md` from disk and concatenates it with the frontend prompt using a `---` separator. This produces the exact GPT system message structure that Yellow.ai uses in production.

**3. `run_conversation(tc_id, script, mock_overrides, system_prompt, tools, client, model, queue)`**
The bot simulator. Replays a test's conversation script against GPT:
- Builds a messages list starting with the system prompt
- For each customer message: appends it, calls GPT (temperature=0), handles any tool calls by returning mocks, detects `[ROUTE TO: X]` in bot text as routing, emits SSE events for every turn and tool call
- Inner loop runs up to 15 times per turn to handle chains of tool calls before the bot speaks
- Returns the full transcript and a per-tool call count dict

**4. `evaluate_transcript(result, criteria, client, model)`**
The LLM judge. Sends the formatted transcript (user messages, bot replies, tool calls with args and results) plus the numbered pass criteria to GPT with `response_format=json_object`. Returns per-criterion PASS/FAIL verdicts, a one-sentence summary, and overall PASS only if every criterion passes.

**5. `generate_tests(requirements, sub_agents, memory_keys, tools, client, model)`**
The test generator. Sends requirements text, sub-agent definitions, memory key definitions, and full tool schemas (including return_schema) to GPT. Returns test cases with conversation scripts, objective pass criteria, and per-test mock overrides calibrated to the tool return schemas.

**6. `diagnose_and_repair(..., target_model)`**
The repair LLM. Constructs a user message containing all 4 platform guide files, reference names, acceptance rules, the current prompt, and failing test details. The system prompt includes a **MODEL OPTIMIZATION GUIDE** from `model_guides.py` for the selected target model. Returns `(diagnosis, new_prompt)` — the repaired prompt must be clean Markdown.

**7. `format_prompt_presentation(...)`**
Final formatting pass when all tests pass — restructures headings and emphasis without changing business logic.

**8. `run_optimization(run_id, uc_id)`**
The main loop. Reads `model` from the run record (set via Run options). Resolves API model vs guideline model via `model_guides.resolve_api_model()`. Runs entirely in a background daemon thread.

### `model_guides.py`
Registry of target models for Run options (`GET /api/models`). Each entry defines display label, OpenAI API model routing, and prompt-writing guidelines injected into repair. Currently: GPT-4.1, GPT-5.1, Claude Sonnet 4.6. Anthropic models use guidelines-only mode (API falls back to Settings model).

### `db.py`
All SQLite operations. Every function uses a context manager that opens a connection, commits on success, rolls back on exception, and always closes. `check_same_thread=False` allows safe use from multiple threads. `conn.row_factory = sqlite3.Row` makes rows dict-accessible; every function converts to plain dicts before returning.

Key functions:
- `init_db(seed_dir)` — creates schema, seeds DAZN use case if DB is empty
- `get_use_case(uc_id)` — returns the full bundle: requirements, current prompt, sub_agents, memory_keys, tools
- `save_prompt(uc_id, content, create_version=False)` — in-place update or new versioned row
- `replace_variables(uc_id, sub_agents, memory_keys)` — atomic delete-and-reinsert
- `replace_tools(uc_id, tools)` — atomic delete-and-reinsert
- `replace_tests(uc_id, tests)` — atomic delete-and-reinsert
- `save_iteration(run_id, n, prompt_text, results, passed, total, diagnosis, new_prompt)` — full iteration snapshot
- `update_run(run_id, **kwargs)` — generic kwargs-to-SET builder

### `prompt_parser.py`
Regex-based parser. Scans prompt text for all Yellow.ai syntax patterns and cross-references them against what's configured for the use case. Used by the 🔍 Parse button.

Detection passes (in order):
1. `{{variable_name}}` — V3 standard memory key references
2. `@[workflow:slug]` — explicit workflow calls; matched text is removed before bracket scan to prevent double-matching
3. `[content]` — classified as: `kb:` prefix → KB lookup, single word → variable in message template, has spaces → agent transfer, kebab-case or other → tool slug
4. `Call ToolName` — bare tool call in instruction text (case-insensitive first char)
5. `@ToolName` — bare @-prefixed tool reference

Each detected item is looked up case-insensitively against configured tools, sub_agents, or memory_keys. Returns `matched: true/false` and a `source` tag showing which pattern detected it.

### `templates/index.html`
Single-page application, 1,172 lines, no build step, no framework. Uses Tailwind CSS from CDN and vanilla JavaScript.

**Layout:**
- Fixed header: use case dropdown, `+` new button, Auto/Step mode toggle, max iterations input, ⚡ Optimize, ▶ Run Tests, ⚙ Settings
- Left sidebar (300px): 6 tab panels (Tests, Reqs, Variables, Tools, Prompt, Runs)
- Main content: live test cards, one per test case

**Global JS state:**
```javascript
let currentUcId  = null;  // selected use case ID
let currentUc    = null;  // full use case bundle from /api/use-cases/<id>
let currentRunId = null;  // active run ID for SSE stream
let currentEs    = null;  // active EventSource object
let _parseResult = null;  // last prompt parse result (for Apply to Config)
```

**SSE event handler (`handleSseEvent`):** dispatches on `event.type` across 13 event types — updates test cards, progress indicators, the step-through modal, and run history in real time.

**Modals:**
- Step-through modal: shows iteration score, diagnosis text, colored unified diff (green = added, red = removed), Continue / Stop buttons
- Parse modal: shows detected variables/tools/agents with match status, + Add per unmatched item, Apply to Config saves all to backend
- Settings modal: OpenAI API key + model, Yellow.ai bot ID + API key + base URL
- Generate overlay: spinner shown during LLM test generation (20–40 seconds)

### `lab_config.json`
Runtime configuration. Written and read by `web_app.py`. Structure:

```json
{
  "openai_api_key":   "sk-proj-...",
  "openai_model":     "gpt-4.1",
  "bot_id":           "x1750679463696",
  "yellowai_api_key": "...",
  "base_url":         "https://nexus.yellow.ai"
}
```

| Field | Used for | Required |
|---|---|---|
| `openai_api_key` | All 4 LLM uses (simulation, evaluation, generation, repair) | Yes |
| `openai_model` | Model for all LLM calls; defaults to `gpt-4.1` if blank | Yes |
| `bot_id` | Yellow.ai bot ID, stored for future live-bot integration | No |
| `yellowai_api_key` | Yellow.ai auth, stored for future live-bot integration | No |
| `base_url` | Yellow.ai regional endpoint, stored for future live-bot integration | No |

API key values are masked in `GET /api/config` — the response shows `•••••••••1234` style. The actual values are never echoed to the browser after saving.

**Do not commit this file** — it contains live API keys.

### `lab.db`
SQLite database. Auto-created at first startup. Contains all use cases, prompt version history, test cases, run records, and per-iteration snapshots. Delete it to fully reset the system — the next boot seeds the DAZN use case from flat files automatically.

**Do not commit this file** — it may contain prompt content or credentials.

### `backend_prompt.md`
The Yellow.ai V3 platform system prompt. This is the backend layer that Yellow.ai prepends to every frontend agent prompt before sending to GPT. It is fixed — the optimization loop reads it but never modifies it.

Contents: execution rules (one action per response, follow steps in order), tool call rules (always silent, use only returned values), memory rules (variables persist across turns), agent routing rules (say escalation message then write routing marker), confidentiality rules (never reveal internal flags or step numbers).

Used by `runner.build_system_prompt()` — concatenated with the current frontend prompt to form the GPT system message.

### `Prompt Guides/agent-prompt-guide.md` (940 lines)
Official Yellow.ai documentation for writing frontend agent prompts. Covers: what a "use case" is, how variables work (`{{variable}}`), how actions work (`[tool-name]`, `[Agent Name]`, `[kb: topic]`), step writing rules, how to use tool results in conditions, how to remember data for later steps, example agents. Loaded into repair LLM context at every iteration.

### `Prompt Guides/complex-steps-guide.md` (1,082 lines)
Official Yellow.ai documentation for multi-step agents with complex branching. Covers: step anatomy, conditional routing trees, passing data between steps, loops and retries, parallel tool calls. Loaded into repair LLM context.

### `Prompt Guides/tools-guide.md` (695 lines)
Official Yellow.ai documentation for tool (workflow) definitions. Covers: what makes a good tool description, parameter schemas, when to call tools, error handling patterns, tool chaining. Loaded into repair LLM context.

### `Prompt Guides/v3-engine-guide.md` (481 lines)
Official Yellow.ai documentation for V3 engine runtime behavior. Covers: what the platform handles automatically (conversation history, memory persistence, step tracking, response formatting), agent selection, execution model. **Critical for the repair LLM** — knowing what the engine already handles prevents it from adding redundant instructions to the repaired prompt. Loaded into repair LLM context.

All 4 guides are loaded once at module import time into the `PROMPT_GUIDES` constant in `runner.py`. Changes to these files require a server restart.

### `requirements.md`
Business requirements for the DAZN Monthly Flex Cancellation use case, written in plain English by a product manager or analyst. Covers authentication rules, subscription identification, pre-cancellation checks, cancellation reason handling, retention offers, pause offers, cancellation impact messaging, and execution rules. Seeded into the `requirements` table on first boot. Used as source of truth for LLM test generation.

### `frontend_prompt_v1.md`
The initial version 1 frontend agent prompt for DAZN Monthly Flex Cancellation. Seeded into the `prompts` table as version 1, `is_current=1` on first boot. This is what the optimization loop starts with. All subsequent versions are stored in the `prompts` table in the DB.

### `tests.json`
Initial test cases for the DAZN use case. Array of objects, each with:
```json
{
  "id": "TC-001",
  "name": "Happy path — standard cancellation",
  "conversation_script": ["I want to cancel my subscription", "Because I don't watch it anymore"],
  "pass_criteria": ["Bot calls getProductDetailsForCancel", "Bot asks for cancellation reason"],
  "mock_overrides": {
    "getProductDetailsForCancel": { "productStatus": "ActivePaid", "inFirst14Days": false, ... }
  }
}
```
Seeded into the `tests` table on first boot. After seeding, tests can be regenerated or edited via the UI.

### `tests.template.json`
Blank template showing the expected JSON schema for test cases. Reference only — not used by the application.

### `master prompt.md`
The original Yellow.ai master prompt reference document, predating the web application. Describes Yellow.ai's overall prompt architecture and conventions. Kept for reference. The active backend prompt used in simulations is `backend_prompt.md`.

### `instructions.md`
The original 27,000-line manual optimization guide describing the full phase-by-phase process: parse requirements, build agent model, generate test cases, write first-draft prompt, run tests, evaluate transcripts, diagnose failures, repair prompt, re-test, iterate. The web application automates this entire document. Kept for reference.

### `api-config.md`
Yellow.ai REST API reference — endpoint URLs, authentication headers, session management, conversation log API, and Python code patterns for calling the live bot. Used by `test_runner.py`. Reference document for future live-bot integration features.

### `bot_config.json`
Yellow.ai bot credentials used by the legacy `test_runner.py`. Contains `bot_id`, `api_key`, `base_url`. **Never commit real keys.** The web application uses `lab_config.json` instead.

### `bot_config.template.json`
Template for `bot_config.json` with placeholder values. Copy this to `bot_config.json` and fill in real credentials when using the legacy test runner.

### `test_runner.py`
The original CLI test runner. Authenticates with the Yellow.ai API, creates a bot session, sends each customer message from the conversation script, waits for the bot response, collects the conversation log, and writes one JSON transcript file per test case to `test_results/`. Predates the web app. Still usable for validating against the live bot:
```bash
python test_runner.py --config bot_config.json --tests tests.json --output test_results/
python test_runner.py --config bot_config.json --tests tests.json --output test_results/ --only TC-003,TC-007
```

### `local_test_runner.py`
An extended version of the test runner with local simulation support, built before the web app as an intermediate tool. Superseded entirely by `runner.py`. Kept for historical reference.

### `requirements.txt`
Python package dependencies. Currently contains only `requests>=2.31.0` (used by the legacy `test_runner.py`). The web application additionally requires `flask` and `openai`:
```bash
pip install flask openai
```

### `test_results/`
Output directory for `test_runner.py`. One JSON file per test case (`TC-001_transcript.json`, etc.), each containing the full conversation log from the Yellow.ai conversation logs API. Not used by the web application.

### `ARCHITECTURE.md`
Extended engineering deep-dive: full architecture diagrams, every design decision with rationale and tradeoffs, complete function-level documentation for `runner.py` and `db.py`, data flow diagrams for every request type, SSE event reference, threading model explanation, and onboarding guide. Use this for deep KT sessions.

### `CLAUDE.md`
Configuration file for the Claude Code AI assistant. Tells Claude how to start a new agent testing session, where to find credentials, how to run tests, and the key rules of the project. Not relevant to normal use — only read by AI coding assistants.

---

## 7. Database Schema

SQLite database (`lab.db`), WAL mode, foreign keys on, cascading deletes throughout.

```
use_cases
  id          INTEGER PRIMARY KEY
  name        TEXT
  created_at  TEXT
  updated_at  TEXT

requirements                          (one-to-one with use_cases)
  id          INTEGER PRIMARY KEY
  use_case_id INTEGER → use_cases(id) CASCADE
  content     TEXT    ← full requirements text

prompts                               (one-to-many, versioned)
  id          INTEGER PRIMARY KEY
  use_case_id INTEGER → use_cases(id) CASCADE
  version     INTEGER
  content     TEXT    ← full prompt text
  is_current  INTEGER ← exactly one row = 1 per use_case_id
  created_at  TEXT

sub_agents                            (routing destinations)
  id          INTEGER PRIMARY KEY
  use_case_id INTEGER → use_cases(id) CASCADE
  name        TEXT    ← must match [Agent Name] in prompt exactly
  description TEXT

memory_keys                           (known variables)
  id          INTEGER PRIMARY KEY
  use_case_id INTEGER → use_cases(id) CASCADE
  key_name    TEXT    ← matches {{variable}} in prompt
  description TEXT

tools                                 (workflow definitions)
  id            INTEGER PRIMARY KEY
  use_case_id   INTEGER → use_cases(id) CASCADE
  name          TEXT    ← must match tool call in prompt exactly
  description   TEXT
  return_schema TEXT    ← JSON string, parsed to dict on read

tests
  id                  INTEGER PRIMARY KEY
  use_case_id         INTEGER → use_cases(id) CASCADE
  test_id             TEXT    ← "TC-001" format
  name                TEXT
  conversation_script TEXT    ← JSON array of customer messages
  pass_criteria       TEXT    ← JSON array of assertion strings
  mock_overrides      TEXT    ← JSON object: tool_name → response dict
  created_at          TEXT

runs
  id                INTEGER PRIMARY KEY
  use_case_id       INTEGER → use_cases(id) CASCADE
  mode              TEXT    ← "auto" or "step"
  status            TEXT    ← "running" | "paused" | "done" | "stopped" | "error"
  total_tests       INTEGER
  current_pass      INTEGER ← updated after each test in the current iteration
  current_iteration INTEGER
  max_iterations    INTEGER
  started_at        TEXT
  ended_at          TEXT

iterations
  id           INTEGER PRIMARY KEY
  run_id       INTEGER → runs(id) CASCADE
  n            INTEGER ← iteration number (1-based)
  prompt_text  TEXT    ← the prompt tested in this iteration
  results      TEXT    ← JSON array of per-test result objects
  passed       INTEGER
  total        INTEGER
  diagnosis    TEXT    ← LLM's root cause analysis (empty if all passed)
  new_prompt   TEXT    ← repaired prompt (empty if all passed)
  created_at   TEXT
```

**Prompt versioning detail:** `save_prompt(create_version=True)` sets `is_current = 0` on all existing rows for the use case, then inserts a new row with `is_current = 1` and `version = MAX(version) + 1`. `save_prompt(create_version=False)` updates the content of the row currently marked `is_current = 1` in place.

---

## 8. API Reference — All Routes

### Config
| Method | Path | Description |
|---|---|---|
| GET | `/api/config` | Returns config with API keys masked |
| POST | `/api/config` | Saves config fields from request body |

### Use Cases
| Method | Path | Description |
|---|---|---|
| GET | `/api/use-cases` | List all use cases (id, name, created_at) |
| POST | `/api/use-cases` | Create new use case; body: `{name}` |
| GET | `/api/use-cases/<id>` | Full bundle: requirements, prompt, sub_agents, memory_keys, tools |
| DELETE | `/api/use-cases/<id>` | Delete use case and all related data (cascades) |

### Sub-components (all PUT — atomic replace of entire list)
| Method | Path | Body | Description |
|---|---|---|---|
| PUT | `/api/use-cases/<id>/requirements` | `{content}` | Save requirements text |
| PUT | `/api/use-cases/<id>/variables` | `{sub_agents: [...], memory_keys: [...]}` | Replace agents and memory keys |
| PUT | `/api/use-cases/<id>/tools` | `{tools: [...]}` | Replace tools list |
| PUT | `/api/use-cases/<id>/prompt` | `{content, create_version: bool}` | Save or version prompt |

### Prompt Operations
| Method | Path | Description |
|---|---|---|
| GET | `/api/use-cases/<id>/prompt/versions` | List all versions (id, version, is_current, created_at, preview) |
| GET | `/api/use-cases/<id>/prompt/versions/<v>` | Get full content of a specific version |
| POST | `/api/use-cases/<id>/prompt/parse` | Body: `{content}`; returns detected variables, tools, agents |

### Tests
| Method | Path | Description |
|---|---|---|
| GET | `/api/use-cases/<id>/tests` | List all test cases |
| POST | `/api/use-cases/<id>/generate-tests` | LLM generates tests from requirements + tools |

### Runs
| Method | Path | Description |
|---|---|---|
| POST | `/api/use-cases/<id>/run` | Start manual test run; body: `{ids?: [...], model?: string}` |
| POST | `/api/use-cases/<id>/optimize` | Start optimization loop; body: `{mode, max_iterations, model?}` |
| GET | `/api/models` | List available target models for Run options |
| GET | `/api/use-cases/<id>/runs` | List all runs with their iteration data |
| GET | `/api/runs/<id>` | Get single run record |
| POST | `/api/runs/<id>/continue` | Resume a paused step-through run |
| POST | `/api/runs/<id>/stop` | Stop a running or paused run |

### Streaming
| Method | Path | Description |
|---|---|---|
| GET | `/api/stream/<run_id>` | SSE event stream; streams until `done` event or 90s idle |

### Frontend
| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `templates/index.html` |

---

## 9. The Optimization Loop — Step by Step

### Setup (User actions in the UI)

```
1. Select or create a use case
2. Variables tab → add sub_agents (routing destinations with descriptions)
                → add memory_keys (variable names with descriptions)
3. Tools tab    → add each tool: name, description, return_schema JSON
4. Reqs tab     → write business requirements in plain language
5. Click "Generate Tests" → LLM reads requirements + tools → creates test cases
6. Prompt tab   → paste the initial frontend prompt
7. Click 🔍 Parse → verify all variables/tools in the prompt are configured
8. Header       → choose Auto or Step mode, set max iterations (default 10)
9. Click ⚡ Optimize
```

### Iteration (Automated in background thread)

```
For n = 1 to max_iterations:

  ① Build system prompt
     backend_prompt.md content
     + "---"
     + "## AGENT INSTRUCTIONS (FRONTEND PROMPT)"
     + current_frontend_prompt

  ② For each test case:
     - Build OpenAI messages: [{role:"system", content:system_prompt}]
     - Emit: test_start

     - For each customer message in conversation_script:
         Append {role:"user", content:message}
         Emit: turn_user

         Inner loop (up to 15 iterations):
           Call GPT (temperature=0, tools=openai_tools, tool_choice="auto")

           If response has tool_calls:
             For each tool call:
               Look up mock_overrides[tool_name]  →  mock response
               Emit: tool_call
               Append {role:"tool", content: JSON.stringify(mock)}
             Loop again (GPT will read mock results and decide what to do next)

           Else (GPT produced a text message):
             Emit: turn_bot
             If "[ROUTE TO:" in message → routing detected, end test
             Else → break inner loop, move to next user message

     - Emit: eval_start
     - Call evaluate_transcript()
         Sends formatted transcript + pass criteria to GPT
         Gets back: [{criterion, verdict, reason}], overall, summary
     - Emit: test_complete (with full results)

  ③ If all tests pass:
     - save_prompt(create_version=True)   ← new version in DB
     - update_run(status="done")
     - Emit: done  →  return (success)

  ④ If any failures:
     - Emit: diagnosing
     - Call diagnose_and_repair(current_prompt, failed_results)
         User message contains:
           PLATFORM GUIDES: [all 4 guide files, ~3,200 lines]
           CURRENT PROMPT: [current frontend prompt]
           FAILED TESTS: [per-test: failed criteria, reasons, last 8 transcript events]
         Returns: (diagnosis_text, new_prompt_text)
     - compute_diff(current, new)  →  [{type, text}] for UI colored diff
     - save_iteration(run_id, n, ...)  ← full snapshot in DB
     - Emit: iteration_complete (with diagnosis, diff, score)

  ⑤ If mode == "step":
     - update_run(status="paused")
     - Emit: paused
     - _step_events[run_id].clear()
     - _step_events[run_id].wait(timeout=3600)  ← blocks here
       (browser sends POST /continue → signal_continue() → Event.set() → unblocks)
     - update_run(status="running")

  ⑥ If stop flag set → break

  ⑦ Apply new prompt:
     - save_prompt(create_version=True)
     - current_prompt = new_prompt
     - continue to next iteration
```

### Termination

| Condition | `status` | What's saved |
|---|---|---|
| All tests pass | `done` | Final prompt as new version |
| Max iterations reached | `done` | Last state, no further changes |
| User stops | `stopped` | State at last completed iteration |
| Unhandled exception | `error` | Traceback printed to server console |

---

## 10. Real-Time Streaming — SSE Events

Events are sent as `text/event-stream` from `GET /api/stream/<run_id>`. The browser handles them via `EventSource`. Each event is a JSON object in the `data:` field.

### Full Event Reference

```javascript
// ── Run lifecycle ──────────────────────────────────────────────
{ type: "run_start",
  run_id: number,
  total_tests: number,
  max_iterations: number }

{ type: "iteration_start",
  run_id: number,
  n: number }           // current iteration number

{ type: "diagnosing",
  run_id: number,
  n: number }           // LLM repair call is in progress

{ type: "iteration_complete",
  run_id: number,
  n: number,
  passed: number,
  total: number,
  diagnosis: string,    // root cause text from repair LLM
  diff: [               // unified diff of prompt change
    { type: "add"|"remove"|"context"|"header", text: string }
  ],
  all_pass: boolean }

{ type: "paused",
  run_id: number,
  n: number }           // step mode: waiting for user Continue

{ type: "done",
  run_id: number,
  passed: number,
  total: number,
  iterations: number,
  final_prompt: string }

{ type: "error",
  message: string }

{ type: "ping" }        // keepalive every 90s of idle — ignored by UI

// ── Per-test events ────────────────────────────────────────────
{ type: "test_start",
  tc_id: string,        // "TC-001"
  name: string,
  run_id: number }

{ type: "turn_user",
  tc_id: string,
  turn: number,         // 1-based turn index
  message: string }     // customer message

{ type: "tool_call",
  tc_id: string,
  turn: number,
  tool: string,         // tool name
  args: object,         // arguments GPT passed (may be empty)
  result: object }      // mock response returned

{ type: "turn_bot",
  tc_id: string,
  turn: number,
  message: string }     // bot's text response

{ type: "eval_start",
  tc_id: string }       // LLM evaluation starting

{ type: "test_complete",
  tc_id: string,
  name: string,
  overall: "PASS"|"FAIL"|"ERROR"|"SKIP",
  summary: string,      // one-sentence summary from evaluator
  results: [            // per-criterion verdicts
    { criterion: string, verdict: "PASS"|"FAIL", reason: string }
  ],
  transcript: [         // full conversation log
    { type: "tool_call", turn, tool, args, result } |
    { type: "bot_message", turn, user, bot }
  ],
  tool_calls_made: {    // total call count per tool
    "toolName": number
  }
}
```

---

## 11. Prompt Parser — Syntax Detection

The parser (`prompt_parser.py`) scans prompt text using regex and cross-references against the use case's configured tools, sub_agents, and memory_keys.

### Detection Patterns (Applied in Order)

| Pass | Pattern | Regex | Classifies as |
|---|---|---|---|
| 1 | `{{variable_name}}` | `\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}` | Variable (V3 standard) |
| 2 | `@[workflow:slug]` | `@\[workflow:([a-zA-Z0-9_]+)\]` | Tool (explicit slug) |
| 3 | `[kb: topic]` | `\[([^\]]+)\]` where starts with `kb:` | KB lookup |
| 3 | `[singleWord]` | `\[([^\]]+)\]` where single word | Variable in message |
| 3 | `[Multi Word]` | `\[([^\]]+)\]` where has spaces | Agent transfer |
| 3 | `[kebab-case]` | `\[([^\]]+)\]` where matches kebab | Tool slug |
| 4 | `Call ToolName` | `\bCall\s+([a-zA-Z][a-zA-Z0-9]{3,})\b` | Tool (bare) |
| 5 | `@ToolName` | `@([a-zA-Z][a-zA-Z0-9]{3,})\b` | Tool (bare @) |

Pass 2 removes matched `@[workflow:slug]` text before pass 3 runs, preventing double-matching.

### Bracket Classification Logic

```
[content]
  ├─ starts with "kb:"  →  KB lookup  (topic is content after "kb:")
  ├─ contains spaces    →  Agent transfer  ([Chat With An Agent])
  ├─ single word        →  Variable reference  ([firstName])
  └─ kebab-case         →  Tool slug  ([fetch-account-details])
```

### Match Results

Each detected item is returned with:
- `name` — the detected identifier
- `source` — which pattern found it (`{{variable}}`, `[camelCase]`, `[Agent Name]`, `Call X`, `@[workflow:slug]`, etc.)
- `matched` — `true` if found case-insensitively in the configured list
- `description` — the configured description if matched, empty string if not

### Parse API Response Shape

```json
{
  "variables": [{ "name": "firstName", "source": "[camelCase]", "matched": true, "description": "" }],
  "tools":     [{ "name": "cancelProduct", "source": "Call X", "matched": false, "description": "" }],
  "agents":    [{ "name": "Chat With An Agent", "source": "[Agent Name]", "matched": true, "description": "Escalate" }],
  "kb_lookups": ["refund policy"],
  "summary": {
    "variables": { "total": 6, "unmatched": 2 },
    "tools":     { "total": 4, "unmatched": 1 },
    "agents":    { "total": 3, "unmatched": 0 }
  }
}
```

### UI Behavior

- Matched items: green ✓ badge
- Unmatched items: amber ! badge + `+ Add` button (marks item as "to add" in `_parseResult`)
- "Apply to Config" button: PUTs updated sub_agents+memory_keys and tools to the API, then re-parses to confirm all matched, then refreshes the Variables and Tools tabs

---

## 12. Frontend — UI Tabs and Operations

The lab uses a **3-step workflow** in the header: **Setup → Test & fix → Live validate**. Sub-tabs change per step.

### Setup (step 1)
- **Requirements** — business requirements text + Generate Tests
- **Variables** — sub-agents and memory keys
- **Tools** — workflow definitions and return schemas
- **Prompt** — current editable prompt (what Optimize uses)

### Test & fix (step 2)

**Tests tab** — sidebar list of test cases with live status dots (pending / running / PASS / FAIL). **Run Tests** is in the panel footer. Click a test to see transcript, criteria, and diff in the main pane.

**Runs tab** — history of simulated optimization runs with per-iteration diagnosis and prompt diffs.

**Versions tab** — read-only prompt version viewer:
- Narrow sidebar (~272px): searchable version picker, **Compare versions** button, metadata (lines, tokens, model), Load to Curr Prompt
- Main pane: Overleaf-style split — **Markdown** source (with line numbers) | **Preview** (rendered with headings, bold, Yellow.ai chips for `{{vars}}`, workflows, rich media)
- Draggable center divider; width persisted in `localStorage`
- Compare modal: Diff / Changes / MD·Preview modes with word-level highlighting and token stats

### Live validate (step 3)
Embeds `live_bot.html` — Playwright tests against the real Yellow.ai bot. Select agents, generate & run tests, review transcripts, add acceptance rules on false negatives. **Past Runs** shows agent name (not run number or "headless"). Tests column is wider (360px) for readability.

### Run options menu
- **Target model** — GPT-4.1, GPT-5.1, Claude Sonnet 4.6 (Anthropic models apply guidelines only; simulation uses Settings API model)
- **Optimization** — Auto / Step mode + max iterations

**Optimize** button starts the loop. **Settings** (⚙) — API key and default model.

### Other tabs (global)
- **Rules** — acceptance rules across agents
- **Knowledge** — knowledge store and rubric

### Tests Tab (detail)
Lists all test cases. Each card shows: test ID, name, status badge (pending / running / evaluating / PASS / FAIL), conversation transcript (user messages, tool calls with args and results, bot replies), and per-criterion verdicts with reasons. Cards are created on page load and updated live via SSE events during a run.

### Reqs Tab
Plain-text textarea for business requirements. Save button calls `PUT /api/use-cases/<id>/requirements`. The "Generate Tests" button in the header reads this content to generate test cases.

### Variables Tab
Two editable lists:
- **Sub-agents** — routing destinations. Each row: name + description. The `name` must match exactly what appears in `[Agent Name]` syntax in the prompt.
- **Memory keys** — known variables. Each row: key_name + description. The `key_name` must match what appears in `{{variable}}` or `[camelCase]` in the prompt.

Save button calls `PUT /api/use-cases/<id>/variables` with the full current list (atomic replace).

### Tools Tab
Expandable cards per tool. Each card: name input, description input, return_schema JSON textarea. The schema is what the LLM uses when generating test mock responses — it must accurately describe the fields and value types the real tool returns.

Save button calls `PUT /api/use-cases/<id>/tools` with the full current list (atomic replace).

### Prompt Tab (Setup)
- Editable current prompt + trigger
- Save / Save as version
- **🔍 Parse** button — runs the prompt parser, opens Parse modal

### Versions Tab
See **Test & fix → Versions** above for the split viewer and compare flow.

### Runs Tab
History of all runs for the current use case, newest first. Each run shows: mode, status, score (passed/total), timestamps. Expandable to show per-iteration rows: iteration number, score, diagnosis text, new prompt diff.

### Settings Modal
Fields:
- OpenAI API Key (masked after save)
- OpenAI Model (default API model; override per run in Run options)
- Yellow.ai Bot ID / API Key / Base URL (used by Live Validate Playwright tests)

### Step-Through Modal
Appears after each iteration when mode = "Step". Shows:
- Iteration number and pass/fail score
- Diagnosis: the repair LLM's root cause analysis
- Prompt diff: colored unified diff (green lines = added, red = removed, gray = unchanged context)
- **Continue** button → `POST /api/runs/<id>/continue` → wakes background thread
- **Stop** button → `POST /api/runs/<id>/stop` → sets stop flag, wakes thread to exit

### Generate Tests Flow
1. User clicks "Generate Tests" header button
2. `saveReqs()` auto-saves current requirements text
3. Shows spinner overlay
4. `POST /api/use-cases/<id>/generate-tests` — takes 20–40 seconds
5. Response replaces `currentUc.tests` and re-renders the Tests tab

---

## 13. Configuration

### `lab_config.json` Fields

```json
{
  "openai_api_key":   "sk-proj-...",
  "openai_model":     "gpt-4.1",
  "bot_id":           "x1750679463696",
  "yellowai_api_key": "wfsN7V7...",
  "base_url":         "https://nexus.yellow.ai"
}
```

### Changing Configuration
Open ⚙ Settings in the UI and save. No restart needed — `load_config()` re-reads the file on every API call.

### Model Selection

**Two levels:**
1. **Settings → Model** — default OpenAI API model for all calls when Run options does not override
2. **Run options → Target model** — per-run selection stored on the `runs.model` column; drives which API model runs sim/eval/repair (OpenAI models) and which **prompt-writing guidelines** are injected during repair

Available target models (see `model_guides.py`): **GPT-4.1**, **GPT-5.1**, **Claude Sonnet 4.6**. Claude models apply Anthropic-specific prompt structure guidelines; API calls fall back to the Settings model.

Repaired prompts are output as **clean Markdown** (`#` headings, **bold** emphasis, lists) regardless of input format.

### Bot Credentials
`bot_id`, `yellowai_api_key`, and `base_url` are stored but not currently used by the optimization loop (which runs fully offline). They are plumbing for a future feature to validate the optimized prompt against the live Yellow.ai bot.

---

## 14. Key Design Decisions

### Local simulation instead of live bot calls
The optimization loop never calls the Yellow.ai API. It simulates the bot by running the same two-layer prompt through GPT locally. **Why:** live bot calls are slow (5–15s/turn), require a published bot with correct credentials, have rate limits, and introduce Yellow.ai session management complexity. Local simulation is faster, cheaper, fully controllable, and runs offline. **Tradeoff:** minor behavioral divergence if Yellow.ai's internal prompt construction differs from our concatenation. In practice this is negligible because we use the identical two-layer structure.

### LLM-as-judge evaluation instead of deterministic assertions
Pass criteria are written in natural language and evaluated by GPT. **Why:** natural language criteria handle paraphrasing, reordering, and nuance that regex cannot. A criterion like "bot asks for cancellation reason before showing the offer" requires semantic understanding, not string matching. **Tradeoff:** LLM evaluation is slightly non-deterministic. Mitigated by `temperature=0` and a strict system prompt ("PASS only if clearly and unambiguously satisfied").

### Atomic replace-all for tools/variables/tests
`PUT /variables`, `PUT /tools`, `PUT /generate-tests` replace the entire list, not individual items. **Why:** no diff/merge complexity, one endpoint instead of five (add/remove/update/reorder/validate), consistent state — the DB always matches exactly what the client sent. **Tradeoff:** concurrent edits from two browser windows overwrite each other. Acceptable for a single-user local tool.

### `threading.Event` for step-through pause instead of polling
The background thread blocks on `Event.wait(timeout=3600)` while paused. **Why:** zero CPU usage while paused, instant response when Continue is clicked, no polling loops. **Tradeoff:** if the server crashes while paused, the thread and Event are gone; the run shows `status=paused` in DB but cannot be resumed. User must start a new run.

### Prompt guides loaded once at startup
All 4 guide files (~3,200 lines total) are read from disk once at module import time into `PROMPT_GUIDES`. **Why:** they never change at runtime; reading them on every repair call would be wasteful for a 10-iteration run. **Tradeoff:** changes to guide files require a server restart.

### SQLite with WAL mode
SQLite in WAL (Write-Ahead Logging) mode allows concurrent readers without blocking writers. **Why:** the SSE endpoint (reader) runs simultaneously with the background optimization thread (writer). Without WAL, reads would block writes and vice versa, causing SSE events to be delayed. **Tradeoff:** WAL creates two extra files (`lab.db-wal`, `lab.db-shm`) during active writes. They are automatically cleaned up when the DB is closed normally.

---

## 15. Running the System

### Requirements

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Flask | any recent |
| openai | ≥ 1.0 (v1 SDK) |
| OpenAI API key | GPT-4.1 access recommended |

### Install

```bash
cd "Prompt optimization & measurement"
pip install flask openai
```

There is no `requirements.txt` — the only two dependencies are Flask (web server) and openai (LLM calls). Both install cleanly on macOS, Linux, and Windows.

### Start the Server

```bash
python web_app.py
```

Expected output:
```
Starting Prompt Optimization Lab on http://localhost:5001
 * Serving Flask app 'web_app'
```

Open **http://localhost:5001** in any browser. The server runs on port 5001 by default (change by editing the `port=5001` argument in `web_app.py`).

### First-time Setup (UI walkthrough)

**Step 1 — Enter your API key**

Click **⚙ Settings** in the top-right corner. Paste your OpenAI API key and choose a model (`gpt-4.1` is the default and recommended). Click **Save**. The key is stored in `lab_config.json` on disk — never in the database, never logged.

**Step 2 — Create a use case**

Click **+ New** next to the use-case dropdown and type a name. This creates a new empty use case in the database.

**Step 3 — Configure Variables**

Click the **Variables** tab on the left panel.

- **Sub-agents** — add one entry per agent your prompt can transfer to. The name must match exactly what you'll write in `[Agent Name]` syntax in the prompt (e.g., `Billing Agent`, `Human Agent`).
- **Memory keys** — add one entry per variable the prompt reads from or writes to, in `{{variable_name}}` or `[camelCase]` syntax (e.g., `termType`, `inFirst14Days`). Add a description to improve test generation quality.

**Step 4 — Configure Tools**

Click the **Tools** tab.

Add one entry per workflow/tool the agent calls. For each tool:
- **Name** — must match exactly what the prompt uses (e.g., `getProductDetailsForCancel`)
- **Description** — plain English description of when the agent calls this tool; used by the test generator to create realistic call scenarios
- **Return schema** — JSON object describing every field the tool returns and its type or allowed values. Use `|`-separated values for enums (e.g., `"productStatus": "Active|Expired|Frozen"`). The richer the schema, the better the mock responses.

**Step 5 — Write Requirements**

Click the **Requirements** tab and write plain-English requirements for the agent. These do not need to be formal — a brain dump is fine. Include:
- What the agent's goal is
- What conditions trigger different paths (refund, escalation, tool call, etc.)
- What exact phrases or disclosures are mandatory
- What the agent should NOT do in certain scenarios

**Step 6 — Generate Tests**

Click **Generate Tests** in the header. The LLM reads your requirements, variables, and tool schemas to produce test cases. Each test has:
- A `conversation_script` — the sequence of customer messages to replay
- `pass_criteria` — specific assertions to evaluate after the conversation
- `mock_overrides` — per-test mock responses for each tool call

Review the generated tests in the **Tests** panel. Edit any test by clicking on it — you can adjust the script, criteria, or mock responses.

**Step 7 — Paste Your Prompt**

Click the **Prompt** tab and paste your initial frontend prompt. If you don't have one yet, write a rough draft — the optimizer will refine it.

**Step 8 — Parse the Prompt (Recommended)**

Click **🔍 Parse**. The parser scans the prompt for all syntax patterns and checks each detected item against your configured variables and tools:

- **Matched** (green) — found in your Variables or Tools config
- **Unmatched** (orange) — referenced in the prompt but not configured

Click **Apply to Config** to bulk-add all unmatched items to the config. Then visit the Variables and Tools tabs to fill in descriptions.

**Step 9 — Run**

Choose a mode:
- **Auto** — runs all iterations unattended. When the run ends (all pass or max iterations), the final prompt is loaded automatically.
- **Step** — pauses after each iteration. A modal shows the repaired prompt diff and iteration score. Click **Continue** to proceed or **Stop** to end early.

Set **Max Iterations** (10 is a good default for complex agents; 3–5 for simple ones).

Click **⚡ Optimize**. The test panel updates in real time — each test card shows its live status (Running → Evaluating → PASS/FAIL) as the background thread progresses.

**Step 10 — Review Results**

Click any test card to see:
- The full conversation transcript (user turns in blue, bot turns in green, routing events in red)
- Per-criterion PASS/FAIL verdicts with LLM reasoning
- Tool calls made (slug, arguments, mock response returned)

**Step 11 — Copy the Final Prompt**

When all tests show **PASS**, the prompt in the **Prompt** tab is your optimized result. Copy it and paste directly into Yellow.ai's frontend agent editor.

### Running Tests Without Optimization (Manual Run)

To run tests against the current prompt without triggering a repair loop:

Click **▶ Run** in the header (without clicking Optimize). This executes one pass of all tests and reports results without modifying the prompt. Useful for regression testing after you manually edit the prompt.

### Reset the Database

```bash
rm lab.db
python web_app.py   # re-seeds from flat files on first boot
```

The seed use case (DAZN Monthly Flex Cancellation) is loaded automatically when the database is empty. Your own use cases are not in files — export them manually before deleting the DB if you want to preserve them.

### Kill a Stuck Server

```bash
lsof -ti :5001 | xargs kill -9
python web_app.py
```

### Run Tests Against the Live Yellow.ai Bot (Legacy CLI)

The `test_runner.py` script sends messages directly to the live Yellow.ai bot via REST API. Use it for final validation after optimization, or for bots where simulation isn't sufficient.

```bash
# copy and fill in credentials
cp bot_config.template.json bot_config.json
# edit bot_config.json with your bot_id, api_key, base_url

# run all tests
python test_runner.py --config bot_config.json --tests tests.json --output test_results/

# run specific tests only
python test_runner.py --config bot_config.json --tests tests.json --output test_results/ --only TC-003,TC-007
```

Transcripts are written to `test_results/TC-XXX_transcript.json`, one file per test case.

---

## 16. How to Add a New Use Case

### Via the UI

1. Click `+` next to the use case dropdown → enter a name
2. **Variables tab:** add sub-agents (routing destinations) and memory keys
   - Sub-agent names must match exactly what the prompt will use in `[Agent Name]` syntax
   - Memory key names must match what the prompt will reference in `{{variable}}` or `[camelCase]`
3. **Tools tab:** add each tool the agent calls
   - Name must match exactly what the prompt will use (e.g., `getProductDetailsForCancel`)
   - Description tells the LLM when to call the tool — be specific
   - Return schema JSON describes the exact fields and value types the tool returns — this directly determines the quality of generated mock responses
4. **Reqs tab:** write business requirements in plain language — the more complete, the better the generated tests
5. Click **Generate Tests** in the header — review and edit generated tests if needed
6. **Prompt tab:** paste your initial frontend prompt
7. Click **🔍 Parse** — verify all variables and tools in the prompt are matched against your configured lists; use "Apply to Config" to add any gaps
8. Header: choose Auto or Step mode, set max iterations
9. Click **⚡ Optimize**

### Via Seed Files (Scripted / Repeatable)

To pre-populate a use case from files (useful for version-controlled setups), add a seed function to `db.py` similar to `_seed()`. Point it to your requirements, prompt, and tests files. Call it from `init_db()` with an appropriate condition (e.g., check for a use case name instead of count = 0).

### Return Schema Best Practices

Good return schemas directly produce good mock responses:
```json
{
  "productStatus": "ActivePaid|ActiveGrace|ActivePaused|Frozen|Expired",
  "zuoraStatus": "Active|Cancelled",
  "inFirst14Days": "boolean",
  "cancellationOptionType": "AUTO_RENEWAL_OFF|IMMEDIATE_NO_FEE|FREE_TRIAL_PLAN",
  "nextChargeDate": "string",
  "penaltyAmount": "number",
  "penaltyAmountCurrencyCode": "string"
}
```
Use `|`-separated enum values for string fields with known values. The test generator reads these and creates mock responses that hit specific branches.

---

## 17. Common Failure Modes and Fixes

### "OpenAI API key not configured"
Open ⚙ Settings, enter the key, save. The key is checked on every LLM operation.

### Port already in use on restart
```bash
lsof -ti :5001 | xargs kill -9
python web_app.py
```

### All tests fail from iteration 1

Check in this order:
1. **`backend_prompt.md` exists** — without it, the system prompt is only the frontend prompt. The routing marker detection (`[ROUTE TO: X]`) and tool call discipline come from the backend prompt. Without it, the bot may behave chaotically.
2. **Mock responses match field names the prompt checks** — if the prompt checks `productStatus` and the mock returns `status`, the condition will never match. Keep return schemas accurate.
3. **Conversation scripts contain only customer messages** — bot replies in the script cause the simulator to get confused. The `conversation_script` array should be only what the customer types.
4. **Pass criteria are objectively verifiable** — vague criteria ("bot handles well") will get inconsistent evaluations. Write specific assertions ("bot calls getProductDetailsForCancel before asking for reason").

### Tests pass locally but fail on the live bot

The simulation uses mocked tool responses. If the live tool returns different field names or different value types than what's in `mock_overrides`, behavior diverges. Keep `return_schema` accurate and `mock_overrides` realistic. For final validation, use `test_runner.py` against the live bot after optimization completes.

### Step-through modal doesn't appear after an iteration

The SSE connection must exist before the background thread emits events. If the browser connects after `iteration_complete` was already emitted, the event is missed. Reload the page and start a new run.

### "run not found" from the SSE stream

The run's queue no longer exists in memory — typically because the server restarted after the run was created. The run record exists in the DB but the thread is gone. Start a new run; the old iteration history is still visible in the Runs tab.

### Repair LLM makes the prompt worse on successive iterations

- Check that `PROMPT_GUIDES` loaded: `python3 -c "import runner; print(len(runner.PROMPT_GUIDES))"` — should be > 0
- Check that failing test transcripts contain meaningful content (not all empty turns)
- Make pass criteria more specific — vague criteria produce vague diagnoses
- Try switching to a stronger model (e.g., `gpt-4o` → `gpt-4.1`) in Settings

### Parse modal shows all items as unmatched

The parser compares against what's in the Variables and Tools tabs. If those tabs are empty, everything is unmatched. Click "Apply to Config" to bulk-add all detected items, then go to the Variables and Tools tabs to fill in descriptions and review.

### DB seeding doesn't run on fresh start

`init_db()` only seeds when `SELECT COUNT(*) FROM use_cases` returns 0. If the DB exists but is empty of use cases for another reason, delete `lab.db` entirely and restart.
