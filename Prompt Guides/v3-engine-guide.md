# V3 Engine — Platform Guide for Agent Creators

This guide explains how the Yellow.ai V3 agent engine works so you can design agents that play well with the platform. Everything here describes the runtime behavior your agent instructions will be executed within.

---

## How Your Agent Gets Selected

The platform uses a **two-stage system** every time a user sends a message:

```
User says: "I want to send money to India"
              ↓
   ┌─────────────────────────────┐
   │  CONTEXT EXPERT (Stage 1)   │   Sees ALL agent titles + triggers
   │  "Which agent handles this?" │   Picks: "Remittance Transaction"
   └──────────────┬──────────────┘
                  ↓
   ┌─────────────────────────────┐
   │  CONVERSATION AGENT (Stage 2)│   Sees ONLY selected agent's instructions
   │  "Follow those instructions" │   + that agent's mapped tools
   └──────────────┬──────────────┘
                  ↓
   Response sent to user
```

### What the Context Expert sees about your agent

The Context Expert sees exactly two things from each agent:

1. **Title** — becomes the agent's slug/identifier
2. **Trigger** — the description it uses to match user intent

```
Available Agents:
- Remittance Transaction: Trigger this flow when user wants to send money, transfer funds...
- Add Beneficiary Optimised: Trigger this flow when the user wants to add a beneficiary...
- Exchange Rate Inquiry: Trigger this flow when user asks about exchange rates...
```

**This is why your trigger text matters so much.** The Context Expert is a small fast model that picks agents based purely on trigger descriptions. Write triggers that are:
- Specific to your agent's domain
- Include example phrases users might say
- Distinct from other agents (avoid overlap)

### How it decides to keep vs switch

The Context Expert follows this logic every turn:

1. Is the user answering a question the current agent asked? → **keep**
2. Is the user mid-way through a multi-step procedure? → **keep**
3. Has the user clearly changed topic to a different agent's domain? → **load** new agent
4. Ambiguous? → **lean toward keep** (continuity wins)

This means once your agent is loaded, it stays loaded as long as the conversation stays on topic. You don't need to worry about being interrupted mid-step.


## What Your Agent Instructions Become

When the Context Expert picks your agent, your instructions get injected into the Conversation Agent's system prompt. Here's what the full prompt looks like:

```
┌─────────────────────────────────────────────────────┐
│  PLATFORM SECTION (you don't control this)          │
│                                                     │
│  • Agent identity & persona                         │
│  • Bot identity (brand name, personality)            │
│  • Conversation guidelines:                         │
│    - Be concise, say only what this turn requires   │
│    - Plan silently before responding                 │
│    - Handle handoffs without restating context       │
│    - Use chat summaries for continuity               │
│  • Confidentiality rules                            │
│  • Data integrity (never fabricate data)             │
│  • Customer-defined rules                           │
│  • Memory system explanation                        │
│  • Response format (JSON)                           │
│  • Execution rules                                  │
├─────────────────────────────────────────────────────┤
│  YOUR AGENT INSTRUCTIONS (you control this)         │
│                                                     │
│  • Your step-by-step procedure                      │
│  • Your tool references                             │
│  • Your variable references                         │
│  • Your rules and special handling                  │
├─────────────────────────────────────────────────────┤
│  STEP EXECUTION RULES (auto-added if steps found)   │
│  (Platform adds this if your instructions have      │
│   numbered steps — you get this for free)            │
└─────────────────────────────────────────────────────┘
```

### What "step execution rules" means for you

If your agent instructions contain numbered steps (like `Step 1:`, `Step 2:`), the platform automatically adds rules that make the LLM:

- Execute **one step per turn** (no skipping ahead)
- Track progress in a memory key called `$$current_steps_progress_info`
- Record which steps are completed, what conditional decisions were made, and which tools were called
- On agent handoff, evaluate from the top and skip steps already satisfied by data in memory

**You get this behavior automatically.** Just write numbered steps and the platform enforces sequential execution.

### The silent planning checklist

Before every response, the platform prompt tells the LLM to silently determine:
1. What step am I on?
2. What does the user need to know NOW that I haven't already told them?
3. What is the single next action — a tool call, a question, or a confirmation?

This prevents the LLM from over-explaining, repeating itself, or jumping ahead. Your instructions benefit from this automatically.


## How Memory Works

Memory is a **persistent key-value store** that survives across conversation turns. It lasts for 2 days per user session.

### Reading memory

The LLM receives ALL current memory as a JSON object in a system message tagged `<ongoing-context>`:

```xml
<ongoing-context>
{
  "country_name": "India",
  "arex_country_code": "IN",
  "selected_beneficiary": "Rahul Sharma",
  "user#firstName": "Mohammed",
  "user#languageName": "English"
}
</ongoing-context>
```

Your agent can reference any of these keys in its instructions. For example:
> "Check `$$VARIABLE(country_name)` to see if the user already selected a country"

### Writing to memory

The LLM includes a `"memory"` field in its JSON response to update keys:

```json
{
  "messages": ["I've selected India for you."],
  "goalStatus": "progress",
  "memory": {
    "country_name": "India",
    "arex_country_code": "IN"
  }
}
```

- **Omitted keys are preserved** — you only send what changed
- **Setting a key to `null` deletes it** from memory
- **Non-string values** (objects, arrays) are supported

### Special memory keys you should know about

| Key pattern | Meaning |
|---|---|
| `user#*` | **Read-only.** User profile data (e.g., `user#firstName`, `user#languageName`). Seeded from the user session. Your agent cannot modify these. |
| `system#*` | **Read-only.** System data. Cannot be modified. |
| `$$current_steps_progress_info` | **Reserved.** The platform uses this to track step execution. Gets set to `null` when procedure completes. |
| Everything else | **Read-write.** Your agent can create, update, and delete any other keys freely. |

### How variables from the platform UI become memory keys

When the bot builder creates variables/inputs in the platform UI, they are converted to memory keys:

| Platform UI reference | Memory key |
|---|---|
| Agent variable `countryCode` | `countryCode` (read-write) |
| Agent input `transferAmount` | `transferAmount` (read-write) |
| User property `firstName` | `user#firstName` (read-only) |
| System property `channelType` | `system#channelType` (read-only) |

This means when your instructions say "store the country code in $$VARIABLE(countryCode)", the LLM writes to the `countryCode` memory key.


## How Tools (Skills) Work

Each agent has a set of **mapped tools** (called "skills" in the platform). When the Context Expert selects your agent, ONLY your agent's tools become available to the LLM.

### What the LLM sees

Tools appear as standard OpenAI function definitions:

```json
{
  "type": "function",
  "function": {
    "name": "getcountrydetailsremitance_megkaf",
    "description": "Retrieves list of applicable countries and their codes",
    "parameters": { ... }
  }
}
```

### How tool execution works

```
Turn 1:  LLM calls tool → engine executes skill → result returned
         LLM sees result → generates response to user
         (This all happens in a single turn — the user sees one response)

Turn 2:  User replies → LLM continues from where it left off
```

The LLM can call **multiple tools in a single turn** if needed. The engine loops:
1. LLM returns tool_calls → engine executes them → results added to history
2. LLM called again with results → may return more tool_calls or a final response
3. Loop continues until LLM returns messages without tool_calls (max 30 iterations)

### Tool failure handling

If a tool fails, the platform rules say:
- **Do NOT retry the same tool** — inform the user about the error
- The error message is returned as the tool result, so the LLM sees what went wrong

### Knowledge Base tool

Every agent also has access to `query_knowledgebase` — a RAG tool that searches the bot's uploaded knowledge base (PDFs, Excel, etc.). This is always available regardless of which agent is selected.


## How Agent-to-Agent Switching Works

Your agent can **trigger another agent** by returning a `trigger` object:

```json
{
  "messages": ["Let me help you add a new beneficiary."],
  "goalStatus": "progress",
  "trigger": {
    "target": "Add Beneficiary Optimised",
    "instructions": "User wants to add beneficiary for India, bank transfer type"
  }
}
```

### What happens next

1. The user sees your `messages` first
2. The engine re-runs the Context Expert to find the target agent
3. The target agent is loaded with your `instructions` as context
4. The target agent receives a `<handoff-event>` tag with the prior conversation

### What the receiving agent sees

```xml
<handoff-event>
You were triggered by another agent. Reason: User wants to add beneficiary for India, bank transfer type

[prior conversation history]
</handoff-event>
```

The platform prompt tells the receiving agent to:
- **Not restate** anything already said
- **Check `<ongoing-context>`** for data already collected
- **Skip steps** whose requirements are already satisfied
- Start from the next action that hasn't been done

### Writing good trigger targets

The `target` should match the **exact title** of the agent you want to trigger. The Context Expert looks it up in the agent index.

Good: `"target": "Add Beneficiary Optimised"` (exact title)
Bad: `"target": "add_beneficiary"` (won't be found)

The `instructions` field should summarize what context the target agent needs — what's been collected, what the user wants, any preferences already stated.

### Child Agent Triggers (Return to Parent)

Sometimes an agent needs to delegate a subtask to another agent and then resume where it left off. Use `returnTo: true` in the trigger to create a parent-child relationship instead of a peer switch.

#### Trigger format

```json
{
  "messages": ["Let me quickly verify your identity."],
  "goalStatus": "progress",
  "trigger": {
    "target": "agent-slug-or-name",
    "instructions": "context for the child agent",
    "returnTo": true
  }
}
```

#### How it works

1. When the parent agent returns a trigger with `returnTo: true`, the engine **snapshots** the parent's state — agent-scoped variables and step tracking (`$$current_steps_progress_info`).
2. The child agent is loaded and runs normally, just like any other agent.
3. When the child agent sets `goalStatus: "completed"`, the engine automatically **restores the parent agent** from the snapshot.
4. The parent receives a `<child-agent-return>` message indicating the child finished, and resumes from where it left off.
5. Full conversation history is preserved — the parent sees everything the child said to the user.

The child can span multiple turns before completing. There is no turn limit specific to child agents.

#### Limitations

- **Maximum 1 level deep.** Only parent → child is supported. Nested children are not allowed.
- If a child agent triggers another agent with `returnTo: true` while a parent is already waiting, the trigger is treated as a **peer switch** (no return). The original parent's snapshot is discarded.
- The snapshot captures agent-scoped state only. Global memory keys set by the child remain available to the parent after return.

#### Comparison with peer triggers

| | Peer Trigger | Child Trigger |
|---|---|---|
| `returnTo` | absent or `false` | `true` |
| Parent state | Cleared | Snapshotted and restored |
| Return | No automatic return | Automatic on child completion |


## How Conversation History Works

### What the LLM sees

The LLM receives the full conversation history as OpenAI-style messages:

```
user: "I want to send money to India"
assistant: {"messages":["I'd be happy to help..."],"goalStatus":"progress",...}
user: "Send to Rahul"
assistant: {"messages":["I've selected Rahul..."],"goalStatus":"progress",...}
user: "10000 dirhams"
```

### History compaction (automatic summarization)

Long conversations get automatically summarized. When history exceeds ~15 messages or ~10,000 characters, older messages are compressed into a summary:

```xml
<chat-summary-sofar>
The user wants to send money to India. Selected beneficiary: Rahul Sharma (HDFC Bank, account 123456).
Transfer amount: 10,000 AED. Service type: Instant Transfer. Exchange rate: 1 AED = 22.68 INR.
</chat-summary-sofar>
```

The last 2 user turns are always kept raw (uncompacted). This means:
- Your agent always has the exact recent messages
- Older context is available as a summary
- The platform prompt tells the LLM to use `<chat-summary-sofar>` for continuity

**Design implication:** Don't rely on the exact wording of messages from 10+ turns ago. Store important values in **memory** instead — memory is never compacted.


## Dynamic Context: What Else the LLM Sees

Every turn, the LLM receives two special tags as a system message:

### `<ongoing-context>`

The current memory state as JSON. This is how the LLM knows what's been collected so far:

```xml
<ongoing-context>
{
  "country_name": "India",
  "arex_country_code": "IN",
  "beneficiary_name": "Rahul Sharma",
  "transfer_amount": 10000,
  "user#firstName": "Mohammed",
  "user#languageName": "English",
  "$$current_steps_progress_info": {
    "currentStep": "Step 3",
    "completedSteps": ["Step 1", "Step 2"],
    "decisions": {"Step 1": "country=India", "Step 2": "beneficiary=Rahul"},
    "toolsExecuted": ["getcountrydetails_abc", "getbeneficiary_xyz"]
  }
}
</ongoing-context>
```

### `<env-info>`

Current date, time, day of week, and channel source:

```xml
<env-info>
Current date: 2026-03-19, Time: 14:30 UTC, Day: Wednesday
Source channel: voice
</env-info>
```

## Customer Rules

Bots can have customer-defined rules that apply to ALL agents. These are injected into the system prompt above your agent instructions. Common examples:

- "Provide answer in neat format"
- "Don't switch language mid-conversation"
- "Normalize money expressions (20k → 20000)"
- "Don't send waiting messages like 'please wait'"
- "Capture all inputs in English regardless of conversation language"

Your agent instructions don't need to repeat these — they're already in the prompt. But you should be aware they exist so your instructions don't contradict them.


## The Response Format

Every turn, the LLM must respond with exactly one JSON object:

```json
{
  "messages": ["Text the user sees"],
  "goalStatus": "progress",
  "memory": {
    "key": "value"
  },
  "trigger": {
    "target": "Agent Title",
    "instructions": "handover context"
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `messages` | Yes | Always an **array** of strings. Each string becomes a separate chat bubble. Usually just one string. |
| `goalStatus` | Yes | `"progress"` (still working) or `"completed"` (procedure done). Only set `completed` when your instructions explicitly indicate completion. |
| `memory` | No | Key-value updates. Omit to make no changes. Set key to `null` to delete. |
| `trigger` | No | Switch to another agent. Only include when your instructions say to trigger another procedure. |

### Important response rules the platform enforces

1. **Single JSON object** — no text before or after the JSON
2. **`messages` is always an array** — never a single string
3. **Don't announce triggers to the user** — triggers are internal routing. Say something natural like "Let me help you with that" not "I'm now switching you to the Add Beneficiary agent"
4. **Don't mark `completed` unless the procedure is truly done** — the platform uses this to track agent lifecycle


## Designing Agents for This Platform — Key Takeaways

### 1. Your trigger text is your agent's resume
The Context Expert picks agents purely by trigger text. Make it descriptive with example phrases.

### 2. Use numbered steps for sequential procedures
The platform auto-adds step tracking. Write `Step 1:`, `Step 2:` etc. and the LLM will execute them one at a time.

### 3. Store important values in memory, not just conversation
Memory persists and is never compacted. Conversation history gets summarized. If a value matters later, write it to memory.

### 4. Reference memory keys with `$$VARIABLE(name)`
This is how the platform maps UI variables to memory. When you say "store in $$VARIABLE(countryCode)", the LLM writes to the `countryCode` memory key.

### 5. Tools are your agent's API layer
Each tool is a backend skill. Reference them by their skill slug. The LLM calls them automatically when your instructions say to "Execute [tool_slug]".

### 6. Agent-to-agent handoff preserves context
When you trigger another agent, include all relevant context in the `instructions` field. The receiving agent gets your conversation history plus your handoff notes.

### 7. Don't repeat platform rules in your instructions
The platform already tells the LLM to be concise, not fabricate data, not retry failed tools, handle languages, etc. Focus your instructions on the business logic.

### 8. The LLM plans silently each turn
It checks: what step am I on, what does the user need to know, what's the next action. Your instructions should make these answers clear at every step.

### 9. One step per turn, one action per step
The platform enforces this. Design each step to do ONE thing — call a tool, ask a question, or confirm data. Don't pack multiple actions into one step.

### 10. Memory survives across agent switches
If Agent A stores `country_name: "India"` in memory, Agent B can read it from `<ongoing-context>`. Use this for data sharing between agents.

---

## Quick Reference: Platform Capabilities

| Capability | How it works |
|---|---|
| **Agent selection** | Context Expert matches user intent to agent trigger text |
| **Step execution** | Auto-tracked when instructions have numbered steps |
| **Memory** | Persistent KV store, survives across turns and agent switches, 2-day TTL |
| **Tools** | Backend skills mapped per agent, called via OpenAI function calling |
| **Agent switching** | Return `trigger` object with target agent title + context (peer switch) |
| **Child triggers** | Add `returnTo: true` to trigger for parent-child delegation with automatic return |
| **History** | Auto-compacted to summaries, last 2 turns always raw |
| **Knowledge base** | RAG tool (`query_knowledgebase`) always available |
| **Language support** | Platform rules handle multilingual, your instructions should capture inputs in English |
| **Voice/streaming** | Responses streamed sentence-by-sentence for TTS on voice channels |

---
*Yellow.ai V3 Agent Engine — Platform Guide for Agent Creators*
