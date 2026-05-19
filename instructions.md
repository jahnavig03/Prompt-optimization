# Yellow.ai Frontend Prompt Framework
## Instructions for Claude

---

## Overview

This document tells Claude how to take a plain-language description of a Yellow.ai agent and produce a frontend prompt that passes automated testing 100% of the time. The process is fully automated: Claude generates the prompt, sends test conversations to the live bot via the Yellow.ai REST API, reads the transcripts, judges pass/fail, diagnoses what went wrong, repairs the prompt, and repeats until every test passes.

**Architecture reminder:**
- The backend prompt is fixed and controlled by Yellow.ai. You cannot edit it.
- The frontend prompt is what you generate and iterate on. It is injected into the LLM alongside the backend prompt.
- All platform behaviors described in `v3-engine-guide.md` are enforced automatically — do not repeat them in the frontend prompt.

**Inputs you need before starting:**
1. A plain-language description of the agent (the "requirements") — what it does, what steps it has, what tools it calls, what validations it enforces, what UI elements it renders, and what routing it follows.
2. The exact registered slugs for all tools (workflows), rich media components, and any sub-agents referenced.
3. The Yellow.ai bot ID and API key for the bot being tested.
4. The REST API endpoint for the bot (check https://docs.yellow.ai/api/send-message-event-to-bot for the exact format).

If any of these are missing, ask before proceeding. A typo in a workflow slug or a missing API key means every test will fail silently.

---

## The Loop at a Glance

```
Requirements
    │
    ▼
Phase 1: Parse & model the agent
    │
    ▼
Phase 2: Generate test cases (BEFORE writing the prompt)
    │
    ▼
Phase 3: Write the first-draft frontend prompt
    │
    ▼
Phase 4: Execute tests via REST API ◄──────────────┐
    │                                               │
    ▼                                               │
Phase 5: Evaluate transcripts (judge pass/fail)     │
    │                                               │
    ├── All pass ──► Done. Output final prompt.     │
    │                                               │
    └── Any fail ──► Phase 6: Diagnose failures     │
                          │                         │
                          ▼                         │
                     Phase 7: Repair prompt ────────┘
                     (max 10 iterations total)
```

---

## Phase 1 — Parse & Model the Agent

Read the requirements carefully and extract a structured model before writing anything. This model is your source of truth for both test generation and prompt writing.

### Extract the following:

**1. Agent identity**
- Name and purpose (one sentence)
- Trigger phrases: exact words/intents that activate this agent
- Hard boundaries: what this agent must refuse to handle

**2. Pre-processing (on activation)**
- Any workflows that must run before the conversation starts
- Any memory variables that must be populated from those workflows

**3. Steps, in order**
For each step, note:
- Step number and name
- Type: `collection` (asks user something), `routing` (calls tool and branches), or `silent` (executes without user interaction)
- The question asked (if collection)
- The tool/workflow called (if any) — with exact registered slug
- The UI element rendered (if any) — with exact registered slug
- Input validation rules (if any)
- Variables written to memory
- Routing: what conditions lead where

**4. Mandatory rules**
- Single-question rule, silent workflow execution, no hallucination, re-ask limits, escalation message, etc.

**5. Reference data**
- Lookup tables, allowed values lists, state-city maps — anything the agent uses for validation

**6. Close conditions**
- Every path that ends the conversation and how

### Output of Phase 1
Write a structured summary in this format before proceeding:

```
AGENT MODEL
===========
Name: [name]
Purpose: [one sentence]
Trigger intents: [list]
Hard boundaries: [list]

Pre-processing:
  - [workflow slug] → saves {{variable}} 

Steps:
  Step 1 — [name] ([type])
    Asks: [question text]
    Renders UI: [slug or none]
    Calls tool: [slug or none]
    Saves: {{variable}}
    Validates: [rules]
    Routes: [condition → step N]
  ...

Mandatory rules:
  - [rule]
  ...

Close paths:
  - [condition] → end
  ...
```

Do not proceed to Phase 2 until this model is complete. Gaps in the model become gaps in test coverage and prompt errors.

---

## Phase 2 — Generate Test Cases

Generate test cases **before** writing the prompt. This keeps test cases objective — they describe what the requirements demand, not what the prompt happens to do.

### Test case categories

For each agent, generate at minimum:

| Category | What it tests | Minimum count |
|---|---|---|
| Happy path — full flow | Complete successful conversation end-to-end | 1 per distinct completion outcome |
| Happy path — alternate routing | Each major branching variation (e.g., book vs. reschedule vs. cancel) | 1 per branch |
| Validation rejection | Invalid input is rejected and user is re-prompted correctly | 1 per validation rule |
| Tool call verification | The correct workflow is called at the right moment | 1 per mandatory tool call |
| UI element rendering | Rich media / quick replies are rendered where required | 1 per UI element |
| Escalation | Max attempts reached → correct escalation message and goal completed | 1 per step with a retry limit |
| Hard boundary | User asks something out of scope → redirected correctly | 1 per declared boundary |
| Edge case | Ambiguous input, partial input, typos in validated fields | 2–3 general cases |

### Test case structure

Write every test case in this exact format:

```
TEST CASE: [TC-NNN] — [Descriptive Name]
Objective: [One sentence — what behavior is being verified]
Category: [happy path / validation / tool call / UI / escalation / boundary / edge]

Conversation script:
  Turn 1 → User: "[exact message to send]"
           Expect: [what the bot MUST do — not exact wording, behavioral description]
  Turn 2 → User: "[exact message to send]"
           Expect: [what the bot MUST do]
  ...

Pass criteria (ALL must be true for TC to pass):
  ✓ [Criterion 1 — observable in transcript]
  ✓ [Criterion 2]
  ✓ [Criterion 3]

Fail indicators (ANY of these = TC fails):
  ✗ [Indicator 1]
  ✗ [Indicator 2]
```

### Rules for writing pass criteria and fail indicators

- Criteria must be **observable in the transcript** — they describe what appears in the conversation log, not internal state.
- Write behaviorally, not by exact string match. "Bot re-prompts for a valid date" not "Bot says 'That doesn't look like a valid date'."
- Include criteria for things the bot must NOT do: "Bot does not ask for the phone number before the state" is a valid criterion.
- Every mandatory tool call must have a corresponding criterion: "Workflow `rescheduleemailtrigger_rlzsfx` is called before the confirmation message."
- Every UI element must have a criterion: "Quick replies widget is rendered (not free-text options)."
- Tool calls and UI renders are visible in the Yellow.ai transcript/log — you will check for them in Phase 5.

### Save the test suite

Number all test cases: TC-001, TC-002, etc. Store the full suite as a reference. You will run these in Phase 4 and track pass/fail per case across iterations.

---

## Phase 3 — Write the First-Draft Frontend Prompt

Now write the frontend prompt. Follow all rules in `master prompt.md`, `agent-prompt-guide.md`, `complex-steps-guide.md`, and `tools-guide.md`. The sample Annual Health Checkup prompt is the reference for format and depth.

### Structural rules (non-negotiable)

1. **Every step is numbered.** The platform's step-tracking engine activates on numbered steps. Unnumbered steps are not tracked and will be skipped.

2. **One action per step.** Never combine a question and a tool call in the same step. Never ask two questions in the same step.

3. **Every collection step has:** the question, the UI element (if any), the variable to save, the validation rule, and the re-prompt behavior.

4. **Every routing step has:** the tool call with exact slug, the variable to save the result into, and a branch for every possible result including failure.

5. **Every branch has a destination.** No dead ends. Every `if` ends with `→ Go to Step N` or `→ Set goal as completed`.

6. **Every loop has a maximum.** State the retry limit explicitly. After the limit: show the exact escalation message and mark goal as completed.

7. **Mandatory rules section.** Always include: single question per message, silent workflow execution, no hallucination, no re-asking captured fields, no internal exposure, silent transitions, universal attempt limits.

### Platform syntax — use exactly these forms

Variables in responses:
```
{{variable_name}}
```

Workflow calls:
```
@[workflow:exact_registered_slug]
```

Rich media / UI elements:
```
@[richMedia:exact_registered_slug]
```

Agent transfers:
```
@[agent:exact_registered_slug]
```

**A single character typo in any slug causes a silent failure.** Copy slugs exactly as registered.

### The pre-processing block

If the requirements specify workflows that run on activation, write:

```
Pre-processing (on activation)
Immediately start workflow: @[workflow:slug].
Call @[workflow:slug] remember output as {{result_variable}}.
```

This must appear before Step 1.

### UI element rendering — make it mandatory

When a step requires a quick replies widget or rich media, write:

```
It is mandatory for you to render [widget name] through @[richMedia:slug].
Do NOT show options as plain text. Always use the widget.
```

Without the word "mandatory" and the explicit "not plain text" instruction, the LLM will sometimes render options as bullet points instead of the widget.

### Validation rules — be explicit and literal

For every validated field, write:
1. The validation rule (with exact logic, not a vague description)
2. The error message for each failure mode (quoted exactly)
3. The re-prompt instruction
4. What NOT to do (e.g., "Do not call any external workflow for spouse DOB validation")

Vague rules like "validate the date" will be interpreted loosely. Explicit rules like "The date must be a valid calendar date. The date must be a past date only. The person must be at least 18 years old as of today using Asia/Kolkata timezone" leave no room for guessing.

### Routing at the end of multi-branch steps

Write routing blocks explicitly at the bottom of each step with branching:

```
Routing:
If {{variable}} is "value A" → Go to Step N.
If {{variable}} is "value B" → Go to Step M.
If {{variable}} is "value C" → Go to Step K.
```

Do not rely on the LLM inferring routing from context. State it explicitly every time.

### Silent execution rule

For any step that calls workflows without requiring user input, add:
```
Do NOT show any intermediate messages (no "please wait", "processing", "checking", etc.) while executing the following.
```

### Mandatory rules block

Always end the prompt with a mandatory rules section covering:
- Single question rule: one question per message, no combining
- Silent workflow execution: no filler messages during tool calls
- No hallucination: never fabricate or infer facts; use only user-provided values or workflow results
- No re-asking: once a field is captured and validated, do not ask again unless the user explicitly requests a change
- No internal exposure: never show variable names, step numbers, workflow names, or system messages to the user
- Silent transitions: no acknowledgement messages ("Got it", "Sure", "Proceeding") between steps
- Universal attempt limit: maximum attempts per validation step, exact escalation message, goal completion on limit

---

## Phase 4 — Execute Tests via REST API

### Setup

All API details, authentication, session management, and Python code patterns are in `api-config.md`. Read it before running any tests. The key points:

- **BOT_ID, API_KEY, BASE_URL** must be provided at the start of each testing session (unique per bot).
- **Two APIs are used together:**
  - `API 1 — Send Message` (POST): sends each user turn and gets the bot's streaming reply. Also captures the `sessionId` from the first response.
  - `API 2 — Conversation Logs` (GET): called once after all turns complete, returns the full structured transcript with tool calls, memory writes, and goal status. Array index [0] is the most recent message — reverse the array before processing.
- Each test case gets a **unique 28-digit numeric `uid`** (generated randomly). The same uid is used for all turns within one test case. A new uid is generated for every new test case.
- Wait **2 seconds** between turns within a test case. Wait **3 seconds** between test cases.

### Running a test case

For each test case, follow the `run_test_case()` pattern in `api-config.md`:
1. Generate a fresh `uid`.
2. Send each user message in the conversation script sequentially via API 1, capturing the `sessionId` from the first response.
3. After the final turn, wait 3 seconds, then call API 2 with `uid` + `sessionId` to retrieve the full log.
4. Reverse the log array (chronological order) and store it against the test case ID.

### Error handling

| Situation | Action |
|---|---|
| API 1 non-200 or empty stream | Retry up to 3 times with 5s wait → mark `ERROR` if still failing |
| `sessionId` not captured | Retry turn 1 once → mark `ERROR` if still missing |
| API 2 empty array | Wait 5s and retry once (logs may lag slightly) |
| Any `ERROR` result | Do not count as prompt failure. Re-run after investigating. |

---

## Phase 5 — Evaluate Transcripts (Pass/Fail)

For each test case, read the transcript and evaluate every pass criterion defined in Phase 2.

### Evaluation approach

Act as an objective judge. For each criterion:
- Is it satisfied? Yes / No / Cannot determine (if the transcript lacks sufficient data)
- If No or Cannot determine → the test case fails.

Be strict. "The bot rendered quick replies" means a widget appeared, not that the bot listed options as text. "The workflow was called" means the tool call log shows the exact slug was invoked, not that the bot said something that implied it might have called the tool.

### Common things to check

**Step execution:**
- Were all mandatory steps executed in order?
- Did any step get skipped that should have run?
- Did any step run that should have been skipped?

**Tool calls:**
- Was every mandatory workflow called?
- Was it called at the right moment (right step, right turn)?
- Were the correct arguments passed?
- Was the result stored in the correct variable?

**UI elements:**
- Was every required widget rendered?
- Was it rendered as a widget (not plain text options)?

**Validation:**
- Did invalid inputs get rejected?
- Was the correct error message shown?
- Was the user re-prompted (not allowed through)?
- Was the retry limit enforced?

**Routing:**
- Did the conversation follow the correct path based on conditions?
- Were there any incorrect branches taken?

**No hallucination:**
- Did the bot state any fact not provided by the user or returned by a workflow?

**Single question rule:**
- Did any message from the bot contain more than one question?

**Silent execution:**
- Did the bot send any "please wait" or filler messages during workflow execution?

### Record results

For each test case, record:
```
TC-NNN: PASS / FAIL / ERROR
Failed criteria (if FAIL):
  - [Criterion that was not met]
  - [Criterion that was not met]
Transcript excerpt (the turn where failure occurred):
  User: "..."
  Bot: "..."
```

If all criteria pass → TC-NNN: PASS.
If any criterion fails → TC-NNN: FAIL with details.

---

## Phase 6 — Diagnose Failures

For each failed test case, identify the failure category and the specific section of the prompt responsible.

### Failure categories

| Category | Symptom in transcript | Root cause in prompt |
|---|---|---|
| **Step skip** | Bot jumps from step A to step C without executing B | Step B is not clearly mandatory; the prompt does not enforce sequential execution; or the routing from A skips B |
| **Validation skip** | Bot accepts an invalid input and proceeds | Validation rule is vague, conditional, or missing for that specific input type |
| **Tool skip** | Required workflow is not in the tool call log | The instruction to call the workflow is ambiguous, conditional when it should be unconditional, or the slug is wrong |
| **UI element skip** | Widget not rendered; options shown as plain text | The render instruction is missing "mandatory" language or the slug is wrong |
| **Hallucination** | Bot states a fact not from user input or workflow result | No explicit "do not fabricate" instruction for that data type; or the bot is inferring from context |
| **Wrong routing** | Conversation takes the wrong branch | Routing conditions are ambiguous or incomplete; variable value doesn't match condition exactly |
| **Multi-question violation** | Bot asks two things in one message | Single-question rule is missing or weakly stated; step combines two collection tasks |
| **Silent violation** | Bot sends filler messages during workflow execution | Silent execution rule is missing for that step or weakly stated |
| **Escalation failure** | Max attempts reached but bot doesn't escalate | Retry limit is not explicitly stated; escalation instruction is missing or unclear |
| **Variable not saved** | A later step can't access data from an earlier step | "Remember as {{variable}}" is missing after the collection step |
| **Goal not completed** | Conversation ends but goalStatus stays "progress" | "Set goal as completed" or "Mark goal as completed" is missing from the close path |

### Write a diagnosis for each failure

```
TC-NNN Diagnosis:
  Category: [failure category]
  Where in the transcript: Turn [N], bot message: "..."
  Which step in the prompt: Step [N] — [name]
  What's wrong in the prompt: [specific text that is missing, wrong, or ambiguous]
  Required fix: [what to add/change/remove]
```

---

## Phase 7 — Repair the Prompt

Apply surgical fixes — change only the sections responsible for the failures. Do not rewrite the entire prompt unless multiple failures span the whole document.

### Repair strategies by failure category

**Step skip**
Add to the failing step:
```
This step is mandatory and must always execute before proceeding to Step [N+1].
Do not skip this step under any circumstances.
```
Also verify the routing from the preceding step points here explicitly.

**Validation skip**
Replace the vague rule with an explicit one. For each input type the rule must validate, write the exact condition, exact error message (quoted), and exact re-prompt instruction. Add "Do not accept any input that does not meet this rule."

**Tool skip**
Add "mandatory" language:
```
You MUST call @[workflow:slug] at this step. This is non-negotiable and must never be skipped.
```
Verify the slug is copied exactly. Add a failure branch if missing.

**UI element skip**
Replace the instruction with:
```
It is mandatory for you to render [widget name] through @[richMedia:slug].
Do NOT list options as plain text. The widget must always be used here.
```

**Hallucination**
Add immediately after the relevant step:
```
Do not fabricate or infer [data type]. Use only the value returned by the workflow or provided explicitly by the user.
```
Also reinforce in the mandatory rules section.

**Wrong routing**
Rewrite the routing block with precise condition matching:
```
Routing:
Evaluate {{variable}} against EXACTLY these conditions in order:
If {{variable}} is "[exact value A]" → Go to Step N.
If {{variable}} is "[exact value B]" → Go to Step M.
If anything else or unclear → [specific fallback].
```

**Multi-question violation**
Split the step into two separate numbered steps. Each step asks exactly one question and waits for a reply before the next step begins.

**Silent violation**
Add to the step:
```
Do NOT send any message to the user while executing the following. Execute silently and respond only with the final output.
```

**Escalation failure**
Add explicitly to the step:
```
Allow up to [N] attempts. On the [N]th failed attempt, immediately say exactly:
"[exact escalation message]"
Then mark the agent goal as Completed.
```

**Variable not saved**
Add after the collection instruction:
```
Remember the user's response as {{variable_name}}.
```
And verify the variable is referenced by the same name in later steps.

**Goal not completed**
Add to every close path:
```
Mark the agent goal as Completed.
```
There must be no path through the conversation that ends without this instruction.

### After repairing

Re-read the repaired sections against the full list of pass criteria for the failed test cases. Verify the fix addresses the criterion before running tests again. Do not run tests on a prompt that still has an obvious gap.

---

## Phase 8 — Re-test and Iterate

After each repair:
1. Re-run **only the failed test cases** from the previous round (plus any tests whose repaired sections could have affected them).
2. Evaluate transcripts as in Phase 5.
3. If all previously-failed cases now pass, run the full test suite once more to confirm no regressions.
4. If any cases still fail, return to Phase 6.

### Iteration tracking

Keep a table:

```
Iteration | Tests run | Passed | Failed | Changes made
----------|-----------|--------|--------|-------------
1         | [N]       | [N]    | [N]    | First draft
2         | [N]       | [N]    | [N]    | Fixed: [categories]
3         | [N]       | [N]    | [N]    | Fixed: [categories]
...
```

### Maximum iterations: 10

If after 10 iterations tests are still failing:
1. Stop the loop.
2. Report which test cases are still failing and their diagnosis.
3. Flag whether the failure appears to be a prompt limitation (the LLM cannot reliably follow this instruction), a test case problem (the criterion is unmeasurable), or a platform limitation (the behavior is controlled by the backend prompt and cannot be influenced by the frontend prompt).
4. For each flagged item, recommend one of: (a) reformulate the prompt instruction differently, (b) adjust the test criterion, (c) accept as a known platform limitation and document it.

---

## Done — Deliver the Final Prompt

When all test cases pass across a full run:

1. Output the final frontend prompt in full.
2. Attach the test suite with all results marked PASS.
3. Include the iteration log.
4. Note any test cases that were revised during the process and why.
5. Flag any areas of the prompt that required more than 3 iterations to fix — these are fragility hotspots that may need re-testing if the agent requirements change.

---

## Reference A — Prompt Checklist

Before running any tests, verify the prompt satisfies every item:

**Structure**
- [ ] Trigger text is keyword-rich and distinct from other agents
- [ ] Hard boundaries are stated explicitly
- [ ] Pre-processing block calls all on-activation workflows
- [ ] All steps are numbered
- [ ] Every step has a descriptive name
- [ ] Every step does exactly one thing

**Collection steps**
- [ ] One question per step
- [ ] UI element render instruction uses "mandatory" language and exact slug
- [ ] Variable name for the captured value is stated
- [ ] Validation rules are explicit and complete (not vague)
- [ ] Error messages are quoted exactly
- [ ] Re-prompt instruction is present
- [ ] Re-ask maximum is stated

**Routing steps**
- [ ] Tool call uses exact workflow slug with `@[workflow:slug]` syntax
- [ ] Result is saved to a named variable
- [ ] Every condition branch has a destination
- [ ] "Not found" and "tool failure" branches exist
- [ ] Silent execution instruction is present

**Routing blocks**
- [ ] All routing decisions are written explicitly at the bottom of the step
- [ ] Variable values in conditions match exactly what the workflow or widget returns
- [ ] Catch-all exists for every multi-branch decision

**Mandatory rules section**
- [ ] Single question rule is present
- [ ] Silent workflow execution rule is present
- [ ] No hallucination rule is present
- [ ] No re-asking rule is present
- [ ] No internal exposure rule is present
- [ ] Silent transitions rule is present
- [ ] Universal attempt limit is present with exact escalation message

**Close paths**
- [ ] Every path that ends the conversation has "Mark the agent goal as Completed"
- [ ] No dead ends exist in the flow

---

## Reference B — Common Prompt Mistakes

| Mistake | What goes wrong | Fix |
|---|---|---|
| Vague validation ("validate the date") | LLM interprets loosely; accepts invalid dates | Write every rule explicitly: calendar check, past-only, minimum age, timezone |
| UI element without "mandatory" | LLM renders options as text sometimes | Add "It is mandatory... Do NOT list as plain text" |
| Tool call without exact slug | Silent failure; tool never called | Copy slug character-for-character from platform |
| Routing by implication | LLM guesses wrong branch | Write explicit routing block at bottom of every branching step |
| "Remember" missing after tool call | Next step can't access the data | Always write "Remember output as {{variable}}" after every workflow call |
| Combining question + tool call in one step | One of them gets dropped | Split into separate steps |
| No escalation limit | Loop runs forever | "Allow up to N attempts. On Nth failure: [exact message]. Mark goal Completed." |
| No "Mark goal as Completed" | Agent stays in progress after closing | Every close path needs this line, explicitly |
| Acknowledging user input between steps | Breaks silent transition rule | Remove "Got it", "Sure", "Okay" from all inter-step messaging |
| Spouse/secondary person validation mixed with appointment tool | Wrong validator called | Add explicit "Do not call appointment validator for this field" |
| Step described as optional when mandatory | Step is skipped | Add "This step is mandatory and must always execute" |
| Variable name inconsistency | Later steps get blank or wrong value | Use the same `{{variable_name}}` throughout — define once, reuse exactly |

---

## Reference C — Yellow.ai Syntax Quick Reference

| What | Syntax |
|---|---|
| Reference a memory variable | `{{variable_name}}` |
| Call a workflow | `@[workflow:exact_slug]` |
| Render a rich media widget | `@[richMedia:exact_slug]` |
| Transfer to another agent | `@[agent:exact_slug]` |
| KB lookup | `[kb: topic]` |

Variables must be saved before being referenced. Slugs must be copied exactly — a single character difference causes a silent failure.

---

*Yellow.ai Frontend Prompt Framework — v1.0*
