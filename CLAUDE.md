# Yellow.ai Prompt Optimization — Claude Code Project

## What This Project Does

Automatically generates, tests, and iterates Yellow.ai frontend agent prompts until they pass 100% of test cases. Claude drives the entire loop: it writes the prompt, calls the live bot via REST API, reads conversation transcripts, judges pass/fail, diagnoses failures, repairs the prompt, and repeats.

---

## File Map

| File | Purpose |
|---|---|
| `CLAUDE.md` | This file — project entry point for Claude Code |
| `instructions.md` | The full framework — read this first, follow it exactly |
| `api-config.md` | API reference: endpoints, auth, session management, Python patterns |
| `test_runner.py` | Python script for sending messages and collecting transcripts |
| `bot_config.json` | Bot credentials for the current session (copy from template, fill in) |
| `bot_config.template.json` | Template for bot credentials — never commit real keys |
| `master prompt.md` | The Yellow.ai master prompt — governs prompt structure and syntax |
| `Prompt Guides/agent-prompt-guide.md` | How to write agents (structure, variables, actions) |
| `Prompt Guides/complex-steps-guide.md` | How to write multi-step branching flows |
| `Prompt Guides/tools-guide.md` | How to define and call tools/workflows |
| `Prompt Guides/v3-engine-guide.md` | How the Yellow.ai V3 engine works (platform behavior) |
| `test_results/` | Output folder — transcripts and pass/fail results per iteration |

---

## How to Start a New Agent Testing Session

### Step 1 — Get credentials from the user
Ask the user for:
- `BOT_ID` — the Yellow.ai bot identifier (e.g., `x1750679469050`)
- `API_KEY` — the x-api-key for this bot
- `BASE_URL` — the regional base URL (e.g., `https://r5.nexus.yellow.ai`)

Write these into `bot_config.json`:
```json
{
  "bot_id": "x________________",
  "api_key": "________________________",
  "base_url": "https://r5.nexus.yellow.ai"
}
```

### Step 2 — Get the agent requirements
Ask the user for a plain-language description of the agent. This can be rough — a brain dump is fine. Also ask for:
- Exact registered slugs for all workflows (tools) the agent uses
- Exact registered slugs for all rich media / quick reply widgets
- Any sub-agents it transfers to

### Step 3 — Follow instructions.md phase by phase
Read `instructions.md` in full, then execute each phase in order:
1. Parse requirements → build agent model
2. Generate test cases (before writing the prompt)
3. Write first-draft frontend prompt
4. Execute tests via `test_runner.py`
5. Evaluate transcripts
6. Diagnose failures
7. Repair prompt
8. Repeat from Phase 4 until all tests pass (max 10 iterations)

---

## Running Tests

### Install dependencies (first time only)
```bash
pip install -r requirements.txt
```

### Run all test cases
```bash
python test_runner.py --config bot_config.json --tests tests.json --output test_results/
```

### Run specific test cases (e.g., after a repair, re-run only failed ones)
```bash
python test_runner.py --config bot_config.json --tests tests.json --output test_results/ --only TC-003,TC-007
```

### Output
`test_runner.py` writes one JSON file per test case into `test_results/`:
```
test_results/
  TC-001_transcript.json
  TC-002_transcript.json
  ...
```

Each file contains the full chronological conversation log from the conversation logs API. Claude reads these to evaluate pass/fail in Phase 5.

---

## Evaluating Transcripts

After `test_runner.py` completes, read each transcript file and evaluate it against the pass criteria defined for that test case in Phase 2. The transcript JSON contains:
- All user messages and bot replies in order
- Tool/workflow calls made (slug, arguments, result)
- UI elements rendered (widget type and options)
- Memory writes (variable name and value)
- Final goal status

Be a strict objective judge. "Workflow was called" means the slug appears in the tool call log — not that the bot's text implied it might have called something.

---

## Key Rules — Never Break These

1. **Generate test cases before writing the prompt.** Test cases must reflect requirements, not what the prompt happens to do.

2. **One action per step in the prompt.** A step either asks a question OR calls a tool OR routes. Never two actions in one step.

3. **Every workflow slug must be exact.** A single character typo = silent failure. Copy slugs directly from the platform, never retype them.

4. **Repair surgically.** Fix only the section responsible for the failure. Do not rewrite the entire prompt unless failures span the whole document.

5. **Mark test results per iteration.** Keep the iteration tracking table (in instructions.md Phase 8) updated after every round.

6. **Never count API errors as prompt failures.** If `test_runner.py` gets a non-200 or empty stream, re-run the test. Only mark FAIL if the bot responded but behaved incorrectly.

7. **Cap at 10 iterations.** If tests are still failing after 10 rounds, report the remaining failures with diagnosis and recommended action — do not continue the loop.

---

## Output at Completion

When all tests pass, deliver:
1. The final frontend prompt (full text, ready to paste into Yellow.ai)
2. The test suite with all results marked PASS
3. The iteration log (how many rounds, what was fixed each round)
4. Any fragility hotspots — sections that took more than 3 iterations to fix

---

## Notes on Platform Behavior

The Yellow.ai V3 engine handles memory, step tracking, tool error handling, response formatting, and conversation history automatically. Do NOT write instructions for these in the frontend prompt — the backend prompt already covers them. Write only the business logic: what to ask, when to call which tool, how to validate, where to route. See `Prompt Guides/v3-engine-guide.md` for the full list of what the platform handles automatically.
