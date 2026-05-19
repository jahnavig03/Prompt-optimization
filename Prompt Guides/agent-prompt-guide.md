# Agent Writing Guide
**Yellow.ai Builder Platform — Business User Reference**

## Contents

- [Agent Writing Guide](#agent-writing-guide)
  - [Contents](#contents)
- [1. What is an Agent?](#1-what-is-an-agent)
- [2. The Two Symbols You Need](#2-the-two-symbols-you-need)
  - [`{{ }}` — Variables](#---variables)
  - [`[ ]` — Actions](#---actions)
- [3. Working with Data](#3-working-with-data)
  - [Where data comes from](#where-data-comes-from)
  - [Using tool results in conditions](#using-tool-results-in-conditions)
  - [Remembering data for later steps](#remembering-data-for-later-steps)
  - [Using remembered data in responses](#using-remembered-data-in-responses)
  - [Writing conditions that check data](#writing-conditions-that-check-data)
- [4. Agent Structure](#4-agent-structure)
- [5. How to Write Steps](#5-how-to-write-steps)
  - [Option A — Plain Prose](#option-a--plain-prose)
  - [Option B — Steps](#option-b--steps)
  - [Step Writing Rules](#step-writing-rules)
- [6. Types of Agents \& Examples](#6-types-of-agents--examples)
  - [6A. Conversational Agent (Prose)](#6a-conversational-agent-prose)
  - [6B. Step-Based Agent](#6b-step-based-agent)
  - [6C. Mixed Agent](#6c-mixed-agent)
- [7. Full Template](#7-full-template)
- [8. What the Platform Handles Automatically](#8-what-the-platform-handles-automatically)
- [9. Best Practices](#9-best-practices)
  - [Writing the agent](#writing-the-agent)
  - [Using variables](#using-variables)
  - [Using actions](#using-actions)
- [10. Common Mistakes](#10-common-mistakes)


# 1. What is an Agent?

An agent is a **procedure written in plain English** that tells the bot how to handle a specific customer situation.

You don't need to know how to code. You write it the way you'd brief a new team member — describe the situation, what to say, what to check, and what to do depending on what the customer says.

Each agent handles **one goal**. If you find yourself writing "and also handle X", that's a second agent.

> **Examples of well-scoped agents**
> - Resolve a sign-in issue
> - Help a customer cancel their subscription
> - Answer questions about pricing
> - Process a refund request

> **Too broad — split these up**
> - Handle all billing issues ✗
> - Help customers with account problems ✗


# 2. The Two Symbols You Need

That's all the syntax there is. Everything else is plain English.

## `{{ }}` — Variables

Use double curly braces to reference a piece of information — something from the customer's account or something collected during the conversation.

```
{{first_name}}               the customer's first name
{{email}}                    the customer's email address
{{subscription_status}}      their current plan
{{blocked}}                  whether the account is blocked
{{device}}                   device type the customer mentioned
{{error_code}}               error code the customer reported
{{help_url}}                 the help centre link
{{reset_url}}                the password reset link
{{brand_name}}               the brand name
```

**You don't have to use `{{ }}` everywhere.** You can describe what you want to remember in plain English and the platform will understand:

```
Remember the customer's device type for later steps.

Save the error code the customer gives you.

Keep the customer's email across the conversation.
```

Both styles work. Use `{{ }}` when you need a value to appear precisely inside an example response. Use plain English when you're giving the agent an instruction about what to track or remember.

**The one rule:** never hardcode things that could change — URLs, prices, brand names. Either use a variable or describe it naturally.

```
✗  "Visit www.help.dazn.com/hc for more help"
✓  "Visit {{help_url}} for more help"
✓  Send them to the help centre

✗  "Hi John"
✓  "Hi {{first_name}}"
✓  Greet the customer by their first name
```

## `[ ]` — Actions

Use square brackets to name a tool to call, search the knowledge base, or transfer to another agent. There are three types of action:

**Tools** — call a tool to fetch data or perform an action:
```
[fetch-account-details]       calls a tool to fetch account data
[send-password-reset]         triggers a reset email to the customer
[process-refund]              processes a refund
```

**Agent transfers** — hand the customer to a different agent:
```
[cancellation-agent]          transfers the customer to that agent
[billing-agent]               transfers to the billing agent
[tier-3-escalation]           escalates to tier 3
```

**KB lookups** — search the knowledge base for information:
```
[kb: password reset]          searches "password reset" in the KB
[kb: geo-restriction errors]  searches that topic in the KB
```

> **How these differ behind the scenes:**
> - `[tool-name]` triggers a tool call — the tool runs and returns data to the agent within the same conversation.
> - `[agent-name]` triggers a transfer — the customer is routed to a different agent. The current conversation context is passed along.
> - `[kb: topic]` triggers a knowledge base search — the agent looks up the topic and uses the result to respond. Use this for reference information (policies, pricing, how-to articles) rather than writing that content directly into the agent.

In a sentence it reads naturally:

```
Fetch the customer's details using [fetch-account-details].

If the customer wants to cancel, send them to [cancellation-agent].

If unsure, check [kb: account not found] before responding.
```

When transferring to another agent, say what context to bring along:

```
Send the customer to [tier-3-escalation] with
{{error_code}} and the IP screenshot.
```

> **Naming must be exact.** The name inside `[ ]` must match the tool slug or agent name as registered in the platform. A typo in `[fetch-account-detials]` means the action silently fails. When transferring to another agent, use the exact agent name — the platform matches by name to find the right agent to route to.


# 3. Working with Data

When the agent calls a tool, the tool returns data. When the customer sends a message, they provide information. This section explains how to use that data in your agent's conditions, responses, and later steps.

## Where data comes from

There are four sources of data the agent can work with:

| Source | How it arrives | Available when |
|---|---|---|
| **Tool results** | The agent calls `[fetch-account-details]` and gets back account data (name, email, status, etc.) | Immediately after the tool call, in the same step and the steps that follow within the same turn |
| **Customer input** | The customer types a reply — an email, an order ID, a description of their issue | After "Wait for reply" |
| **Knowledge base** | The agent calls `[kb: refund policy]` and gets back policy text | Immediately after the KB lookup |
| **Remembered data** | Data you told the agent to "remember" in a previous step | Any step after it was saved — persists across the entire conversation |

**The key rule:** tool results and customer input are available right away, but if you need them in a **later step** (especially one that happens on a different conversation turn), you must tell the agent to **remember** them.

## Using tool results in conditions

After calling a tool, you can write conditions against the data it returns. Just describe what you're checking in plain English — the agent reads the tool result and matches your condition.

```
Fetch the customer's account using [fetch-account-details].

If the account is blocked → Go to Step 4
If the subscription status is "expired" → Go to Step 5
If no account is found → Go to Step 6
If the tool call fails → Go to Step 7
Otherwise → Go to Step 3
```

You don't need to write code or reference field names from the tool's output schema. The agent understands what "account is blocked" means because the tool returns a field like `is_blocked: true`. Just describe the condition naturally.

**What you DO need to know:** the tool's description and output shape determine what conditions you can write. If the tool doesn't return subscription status, you can't check it. Check the tool guide or ask the tool creator what fields are available.

```
✗  If the account's internal_risk_score is above 0.7
   (You can't check this unless the tool actually returns it)

✓  If the account is blocked
   (The tool returns an is_blocked field — this works)
```

## Remembering data for later steps

Tool results are available immediately, but they don't automatically persist. If a later step needs data from a tool call or from the customer, tell the agent to remember it.

```
Fetch the customer's account using [fetch-account-details].
Remember the subscription status and account ID
— you'll need them in the troubleshooting steps.
```

```
Ask the customer what error message they're seeing.
Wait for reply.
Remember the error message — you'll need it if
you escalate later.
```

**When to use "remember":**
- The data is needed in a step that happens on a **different conversation turn** (after a "Wait for reply")
- The data is needed for an **escalation or transfer** at the end of the flow
- You want the data available in the **closing step** for a summary

**When you DON'T need "remember":**
- The next step uses the data immediately (same turn, no wait) — the agent already has it
- The data is a one-time check that isn't referenced again

You can use `{{ }}` to name the data explicitly:

```
Remember the error code as {{error_code}}.
Remember the customer's device type as {{device}}.
```

Or just describe it naturally:

```
Remember the error message and the device type
for the escalation step.
```

Both work. Use `{{ }}` when you want the exact variable name to appear in a later example response.

## Using remembered data in responses

Once data is remembered, reference it with `{{ }}` in example responses:

```
"I can see your subscription is {{subscription_status}}.
Your account was created on {{created_at}}."
```

Or describe it naturally in instructions:

```
Tell the customer their current subscription status
and when their account was created.
```

The agent will pull the values from what it remembers and fill them in.

## Writing conditions that check data

Conditions are the decision points in your agent. Here's how to write them for different data sources:

**Checking tool output (immediately after a tool call):**
```
Fetch order details using [fetch-order-details].

If the order status is "delivered" → ...
If the order status is "in transit" → ...
If the order was placed more than 30 days ago → ...
If the refund amount is greater than £50 → ...
If no order is found for that ID → ...
```

**Checking customer input (after "Wait for reply"):**
```
Ask the customer which plan they're interested in.
Wait for reply.

If they name a specific plan → ...
If they want to compare plans → ...
If they ask something unrelated → ...
If they don't respond or say they're not sure → ...
```

**Checking remembered data (from a previous step):**
```
If the customer's subscription is active → ...
If the error code saved earlier matches a known issue
in [kb: common error codes] → ...
If the customer was already verified in Step 2 → ...
```

**Checking multiple conditions together:**
```
If the account is active AND the region is US → ...
If the order is delivered AND it was more than 48 hours ago → ...
If the customer is verified AND the refund amount is under £50 → ...
```

**Common patterns:**

| What you're checking | How to write it |
|---|---|
| A field from tool output | "If the account is blocked", "If the status is active" |
| Whether a tool found anything | "If no account is found", "If the order exists" |
| Whether a tool succeeded | "If the tool call fails", "If [fetch-order] returns an error" |
| What the customer said | "If they confirm", "If they provide an email", "If they say yes" |
| A value from memory | "If the error code from earlier is X", "If their subscription is expired" |
| A comparison | "If the amount is greater than £50", "If the date is more than 30 days ago" |
| Combined conditions | "If active AND in the US", "If verified AND under the refund limit" |

**Rules:**
1. **Always describe what you're checking, not how.** Write "If the account is blocked", not "If is_blocked equals true".
2. **Always include the negative case.** If you check "If the order is found", also handle "If no order is found."
3. **For tool calls, always handle failure.** "If the tool call fails" is a condition you should always include for critical tools.
4. **Don't assume data exists.** If a previous step might not have saved the data (e.g., the customer skipped it), check for its presence: "If you have the customer's email from an earlier step."


# 4. Agent Structure

Every agent has five parts. Keep them in this order.

```
AGENT: [Name]

WHEN TO USE THIS
When should this agent activate?
What should it NOT handle?

GOAL
One sentence. What does success look like?

BEFORE YOU START
Data to fetch or conditions to check before
saying anything to the customer.

THE CONVERSATION
What the agent actually says and does.

HOW TO CLOSE
What to say when done — resolved or not.
Include "Mark the goal as completed." when done.
```

> **How these map to the platform:**
>
> | Section | Where it goes | Who reads it |
> |---|---|---|
> | **AGENT name** | The agent's title in the platform | Shown in the builder UI and used as the routing identifier |
> | **WHEN TO USE THIS** | The agent's trigger/description field | The **routing AI** reads this to decide which agent to activate — see below |
> | **Everything else** (GOAL → HOW TO CLOSE) | The agent's instruction content | The **conversation AI** reads this while talking to the customer |
>
> This distinction matters. "WHEN TO USE THIS" is **not** read by the same AI that talks to the customer — it's read by a separate routing AI that sees *all* agent descriptions at once and picks the best match for the current message. The rest of the content (GOAL through HOW TO CLOSE) is only loaded *after* the routing AI selects this agent.

### Writing "WHEN TO USE THIS"

This section is the **most important part for routing**. A routing AI reads all agent descriptions at once and picks the best match. Write this section with that in mind:

**Be keyword-rich.** Use the words customers actually say. "Sign-in", "login", "can't access my account", "locked out" are all ways to describe the same problem. Include the key variants.

```
✓  Use this when the customer cannot sign in, is locked
   out, gets a login error, or says they can't access
   their account.

✗  Use this for sign-in issues.
   (too terse — misses common phrasings)
```

**Be differentiating.** If you have similar agents, the routing AI needs clear signals to tell them apart. State what this agent handles AND what it doesn't — that contrast is what makes routing accurate.

```
✓  Use this for refund requests on specific charges.
   Do NOT use for general billing questions (send to
   [billing-agent]) or subscription cancellations
   (send to [cancellation-agent]).

✗  Use this when the customer has a billing issue.
   (overlaps with billing-agent — routing will guess)
```

**Keep it concise.** The routing AI sees every agent's description at once. Two to four sentences is enough. Long descriptions dilute the signal.


# 5. How to Write Steps

Inside **THE CONVERSATION**, you have two ways to write the flow depending on how complex it is.

## Option A — Plain Prose

Use this when the flow is mostly linear with few branches. Just write naturally.

```
Greet the customer and confirm what they need help with.

  "Hi {{first_name}}, thanks for contacting {{brand_name}}.
  How can I help you today?"

Ask for their account email if you don't already have it.
Wait for their reply before continuing.

Once you have their email, look up their account using
[fetch-account-details]. If no account is found, ask
if they may have used a different email address.
```

## Option B — Steps

Use this when there are multiple branches, conditions, or the flow is long. Start each step with `## Step N — Name`.

```
## Step 1 — Greet the customer
## Step 2 — Verify identity
## Step 3 — Check account status
```

Branching inside a step — use plain if/otherwise language with `→` to point to the next step:

```
## Step 3 — Check account status

Check whether the account is blocked.

If the account is blocked → Go to Step 4
If the account is not blocked → Go to Step 5
```

Waiting for the customer:

```
Ask the customer what device they are using and
what error message they see.

Wait for reply.

→ Go to Step 5
```

Skipping steps:

```
If verification is not needed → Skip to Step 4
```

## Step Writing Rules

- **One thing per step.** If a step is greeting AND verifying AND checking, split it into three steps.
- **Every step that asks a question needs a branch.** Don't leave a question without defining what to do with each possible answer.
- **Give every step a name**, not just a number. `## Step 3 — Check account status` is clearer than `## Step 3`.
- **The last step should always close the conversation** — either resolved or handed off. Never let the flow just stop.


# 6. Types of Agents & Examples

## 6A. Conversational Agent (Prose)

Best for: FAQ-style agents, simple lookups, single-topic answers with little branching.

```
AGENT: Answer Pricing Questions

WHEN TO USE THIS
Use this when the customer is asking about subscription
prices, plan options, or what is included in each plan.

Do not use this if the customer wants to upgrade or
downgrade — send them to [plan-change-agent] instead.
Do not use this if they are asking about a charge on
their bill — send them to [billing-agent].

GOAL
The customer understands their plan options and pricing
clearly and feels confident to make a decision.

BEFORE YOU START
Check [kb: current pricing plans] to make sure you are
using the most up to date pricing information. Do not
quote prices from memory.

THE CONVERSATION

Greet the customer and acknowledge what they are looking for.

  "Hi {{first_name}}, happy to help with pricing information!"

Use [kb: current pricing plans] to answer their question.
Keep the response clear and concise — don't list everything
at once. Answer what they asked first, then offer to go
deeper if they want.

If they ask about a specific plan, explain what is included
and the price. If they ask to compare plans, walk through
the differences in a simple way.

If they have a question you cannot answer from the KB,
be honest and direct them to the help centre.

Ask if there is anything else they need.

HOW TO CLOSE

  "Hope that helps! If you'd like to explore your options
  further, you can always check our help centre at
  {{help_url}}. Is there anything else I can help you with?"

If nothing else needed, mark the goal as completed.
```


## 6B. Step-Based Agent

Best for: multi-stage flows, troubleshooting, verification — anything with multiple branches and conditions.

```
AGENT: Resolve Sign-In Issue

WHEN TO USE THIS
Use this when the customer cannot sign into their account.

Do not use this if the customer is asking about cancellation,
billing, or plan changes — redirect them to the relevant
agent for those topics, even if they mention sign-in during
those conversations.

GOAL
The customer is successfully signed in, or has been
escalated to the technical team with full context if
the issue could not be resolved.

BEFORE YOU START
Fetch the customer's account using [fetch-account-details].
If this fails, retry once before continuing.

Fetch their subscription using [fetch-subscription-details].
If this fails, retry once before continuing.

If no account is found for the email, it means there is no
account linked to that address. Remember this — you will
need to raise it with the customer in Step 3.

THE CONVERSATION

## Step 1 — Greet the customer

Greet the customer by first name and confirm you are
helping with their sign-in issue.

  "Hi {{first_name}}, thanks for contacting {{brand_name}},
  my name is {{agent_name}}. It looks like you're having
  some trouble signing in — is that right?"

→ Go to Step 2


## Step 2 — Verify identity

Check whether verification is needed.

If no verification needed → Skip to Step 3

If verification is needed and you have their email:
  "Could you please complete this email address
  {{masked_email}} and confirm you are the account owner?"

If verification is needed but you have no email:
  "Could you please confirm the email address on the
  account and verify that you are the account owner?"

Wait for reply.

If they confirm ownership → Go to Step 3
If they cannot verify → let them know you cannot proceed
  without verification → Go to Step 8 (Close)


## Step 3 — Check account status

If no account was found for their email:
  "I wasn't able to find an account linked to that email
  address. Is it possible you signed up with a different
  email?"

  Wait for reply.
  If they provide a new email → remember the new email
    and go back to Step 2
  If they are unsure → check [kb: account not found]
    and go to Step 8 (Close)

If the account is blocked → Go to Step 4
If the account is not blocked → Go to Step 5


## Step 4 — Blocked account

Let the customer know and collect the following to verify:
  - Full name
  - Registered email address
  - First 6 and last 4 digits of their payment card
    and the expiry date

Cross-check these against the account details.

If verified → let the customer know you are escalating
  to the relevant team. Do not make any account changes.
  → Go to Step 8 (Close)

If they cannot provide the details:
  "Unfortunately I'm not able to proceed without verifying
  your identity. Please contact us again when you have
  those details to hand."
  → Go to Step 8 (Close)


## Step 5 — Gather information

Before troubleshooting, ask the customer:
  - When did the issue start?
  - What error message are they seeing?
  - Is it happening on all devices or just one?

  "Before we dig in, could I ask a few quick questions?
  When did this start, what error are you seeing, and is
  it affecting all your devices or just one?"

Remember what they tell you — you'll need it if you escalate.

Wait for reply.

→ Go to Step 6


## Step 6 — Troubleshoot

Work through the following with the customer one at a time.
Stay empathetic throughout — if they express frustration,
acknowledge it before moving on.

  "I completely understand how frustrating this must
  be — let's get this sorted for you."

- If they are signed in on another device, ask them to
  sign out there first, then try again.
- Ask them to sign out and back in.
- Ask them to try a different browser or incognito mode.
- Ask them to clear cookies and cache, then try again.
- Ask them to reset their password using {{reset_url}}.
- Ask them to try a different network or mobile data,
  especially if on a corporate or Zscaler network.
- Ask if they are using a VPN or proxy — if so, ask
  them to turn it off.
- Ask for a screenshot of the error and their IP address
  from [kb: how to get ip address]. Remember the screenshot.

If the customer is in Japan and the issue is with a
DOCOMO account on a TV → Go to Step 7

If any step resolves the issue:
  "Great, I'm glad that's sorted! Is there anything
  else I can help you with?"
  → Go to Step 8 (Close)

If none of the steps resolve the issue, and you have the
IP screenshot and confirmed no VPN is active:
  "Thanks for your patience. I'll escalate this to our
  technical team now — please stay connected while I
  transfer you."
  Send to [tier-3-escalation] and bring the error code,
  IP screenshot, and a note of the steps already completed.
  → Go to Step 8 (Close)


## Step 7 — DOCOMO TV login (Japan only)

Only use this step if the customer is in Japan AND the
issue is specifically signing into a DOCOMO account on a TV.

Follow the steps in [kb: docomo tv login process] and walk
the customer through them one at a time.

If still not resolved, ask the customer to send a full
video recording of the login process and the error.
→ Go to Step 8 (Close)

HOW TO CLOSE

## Step 8 — Close

  "Thanks for contacting {{brand_name}}. If you need help
  in the future, our help centre has answers to the most
  common questions: {{help_url}}.

  You'll also receive a short survey — your feedback
  means a lot to us."

Add tag: Auto_Assist_Signinissues_used
Do not change the ticket status to Solved.
Mark the goal as completed.
```


## 6C. Mixed Agent

Best for: flows that start conversational, then branch into structured steps for specific paths. Use prose for the simple early stages, then switch to steps when the flow gets complex.

```
AGENT: Process Refund Request

WHEN TO USE THIS
Use this when a customer is requesting a refund for a
charge on their account.

Do not use this for general billing questions — send
those to [billing-agent]. Do not process refunds over
£50 — escalate those to [billing-agent] with a note
that manual approval is required.

GOAL
The customer's refund request is processed and confirmed,
or they have been clearly told why it cannot be processed
and what their options are.

BEFORE YOU START
Fetch account and billing details using [fetch-account-details]
and [fetch-billing-history]. If either fails, retry once.
Remember the billing history — you'll need it to confirm
the charge with the customer.

THE CONVERSATION

Greet the customer and acknowledge the refund request
with empathy.

  "Hi {{first_name}}, I'm sorry to hear there's been an
  issue with a charge. Let me look into this for you."

Ask which charge they are referring to and confirm the
amount and date against their billing history.

Wait for reply.

Once you have confirmed the charge, the flow depends
on the situation:


## Step 1 — Check refund eligibility

Check [kb: refund policy] for the current refund window.

If the charge is within the refund window → Go to Step 2

If the charge is outside the refund window:
  "Unfortunately this charge falls outside our refund
  window. I completely understand that's frustrating —
  let me check what options are available."
  Check [kb: refund exceptions] for any applicable exceptions.
  If no exception applies → Go to Step 4
  If an exception applies → Go to Step 2


## Step 2 — Confirm and process refund

Confirm the details with the customer before processing.

  "I can see a charge of {{charge_amount}} on
  {{charge_date}}. I'll go ahead and process a refund
  for this — can you confirm that's the one you mean?"

Wait for confirmation.

Process the refund using [process-refund].

→ Go to Step 3


## Step 3 — Confirm refund issued

  "Your refund of {{charge_amount}} has been processed
  and should appear in your account within 3–5 business
  days. Is there anything else I can help with?"

→ Go to Step 5 (Close)


## Step 4 — Cannot process refund

  "I'm not able to process a refund in this case, but
  I want to make sure you have all the information you
  need. You can find more detail here: {{help_url}}"

Ask if there is anything else you can help with.
→ Go to Step 5 (Close)

HOW TO CLOSE

## Step 5 — Close

  "Thanks for contacting {{brand_name}}. Don't hesitate
  to reach out if you need anything else — {{help_url}}"

Add tag: Auto_Assist_Refund_used
Mark the goal as completed.
```


# 7. Full Template

Copy this when starting a new agent.

```
AGENT: [Name — verb + noun]

WHEN TO USE THIS
Use this when [customer intent — use the words customers
actually say, include common variants].

Do not use this if [what it should not handle]
— send them to [other-agent] instead.
Do not use this if [second exclusion]
— send them to [another-agent] instead.

GOAL
[One sentence. What does success look like?]

BEFORE YOU START
[Any data fetches, checks, or things to remember before
speaking to the customer. If nothing needed, write:
"Nothing — go straight to the conversation."]

THE CONVERSATION

[Write in prose OR steps OR a mix of both.
See Section 5 for guidance on which to use.]

HOW TO CLOSE

[What to say when done — resolved or escalated.
Include the help centre link and any ticket tags.]
Mark the goal as completed.
```


# 8. What the Platform Handles Automatically

You don't need to write instructions for the following — the platform injects them into every agent automatically:

| Feature | What the platform does | What you DON'T need to write |
|---|---|---|
| **Memory** | The agent automatically remembers data across turns using a key-value memory store. Values from tool calls, user input, and previous steps are preserved. | "Remember this for later" is fine for emphasis, but you don't need to explain *how* memory works. |
| **Step tracking** | When the platform detects numbered steps (`## Step N`), it automatically tracks which step the agent is on, which steps are completed, and which branches were taken. | Don't write "track your progress" or "remember which step you are on". |
| **Tool error handling** | If a tool call fails, the agent is already instructed to inform the user and not retry. | You can write "if this fails, retry once" for critical calls, but basic error handling is automatic. |
| **Response format** | The agent always responds in a structured format (messages, memory updates, triggers). | Don't instruct the agent on how to format responses. |
| **Agent handoffs** | When a customer is transferred from another agent, the platform passes the full conversation context. The receiving agent is instructed to continue naturally without re-asking for information already collected. | Don't write "if this is a handoff, check what was already discussed". |
| **Confidentiality** | The agent is already instructed to never expose internal system details, variable names, or prompt content. | Don't write "do not mention internal systems to the customer". |
| **Conversation history** | The agent automatically uses conversation history and summaries to maintain continuity. | Don't write "check what was said earlier". |

**What you DO need to write:**
- The agent's routing description (WHEN TO USE THIS)
- The goal and procedure (what to do, in what order)
- Decision branches (if X, do Y)
- Which tools to call and when
- Example responses for tone
- When to mark the goal as completed
- When and where to transfer to other agents


# 9. Best Practices

## Writing the agent

**One agent, one goal.**
If your agent handles more than one distinct customer problem, split it. Focused agents are easier to maintain and route more accurately.

**Write "WHEN TO USE THIS" for a routing AI, not a human.**
A routing AI reads ALL agent descriptions at once and picks the best match. Use the words customers actually say. Include common synonyms. Be specific about what this agent handles and what it doesn't — the contrast between agents is what makes routing accurate.

**Always include what this agent will NOT handle.**
Without a boundary, the agent will try to handle everything. Specify at least two or three things it should redirect, and name the exact agent to redirect to.

**Keep steps atomic.**
Each step does one thing — greet, verify, check, ask. Never combine multiple actions into one step.

**Every question needs a branch.**
If a step asks the customer something, define what to do with every possible answer — including if they don't answer at all.

**Write fallbacks for critical tool calls only.**
Basic error handling is automatic (see Section 8). You only need to write explicit fallback instructions for tool calls that are critical to the flow — e.g., "If [fetch-account-details] fails, retry once. If it still fails, ask the customer to confirm their email and try again."

**Use example responses for every step that speaks to the customer.**
Put them in quotes. They anchor the tone and make sure every agent sounds consistent. You don't need more than one or two per step.

**Always mark the goal as completed in HOW TO CLOSE.**
The agent will not end the conversation unless you explicitly say "Mark the goal as completed." Without this, the agent stays in "progress" mode indefinitely.

## Using variables

**You can write naturally instead of using `{{ }}`** when giving instructions to the agent. Both of these are valid and mean the same thing:

```
Remember the error code the customer gives you.
Save {{error_code}} for the escalation.
```

Use `{{ }}` when the value needs to appear inside an example response. Use plain English everywhere else — "greet the customer by name", "use the help centre link", "remember the device type for later".

**Never hardcode things that could change.** URLs, prices, team names, brand names should always be referenced by variable or described naturally — never written out literally inside the agent.

**Name variables clearly** — `{{error_code}}` not `{{e}}`. Someone else will read this agent.

## Using actions

**Know the three types of `[ ]` action:**
- `[tool-name]` — calls a tool, returns data to the agent. The conversation continues.
- `[agent-name]` — transfers the customer to a different agent. The conversation moves.
- `[kb: topic]` — searches the knowledge base for reference information. Use for policies, pricing, how-to content. Don't copy-paste this content into the agent — reference it so it stays in sync.

**Use `[kb: topic]` instead of copy-pasting content.**
Never write policy details, pricing, or process steps directly into the agent. Reference the KB. If the policy changes, you update the KB once — not every agent.

**When transferring to another agent, say what context to bring.**
"Send to [billing-agent] with the issue summary and the customer's email."
Don't make the next agent start from scratch.

**Use the exact name of tools and agents as registered in the platform.**
A typo in `[fetch-account-detials]` means the action silently fails. When transferring to another agent, the name must match the agent's title exactly — the platform uses it to find the right agent to route to.


# 10. Common Mistakes

| Mistake | Why it's a problem | Fix |
|---|---|---|
| Writing compound steps | Agent loses track when something fails | One action per step |
| Hardcoding URLs or prices | Goes stale, inconsistent across agents | Use `{{variable}}` or describe it naturally |
| Vague "WHEN TO USE THIS" | Wrong agent activates, or this agent never activates | Use customer language, include synonyms, state exclusions clearly |
| No anti-triggers in WHEN TO USE | Overlapping agents — routing guesses wrong | Always define what this agent does NOT handle, and name the correct agent for each exclusion |
| Leaving branches undefined | Agent guesses — and gets it wrong | Every if needs an else |
| Copy-pasting KB content into the agent | Goes out of sync when KB updates | Always use `[kb: topic]` |
| No closing step | Conversation just stops | Every agent ends at a named close step |
| Not marking goal as completed | Agent stays active after the conversation should end | Add "Mark the goal as completed." in HOW TO CLOSE |
| Skipping example responses | Tone inconsistency between agents | At least one example response per customer-facing step |
| Transferring without passing context | Next agent asks customer to repeat themselves | Always say what to bring when transferring |
| Misspelled tool or agent name | Action silently fails — agent continues without the data | Copy names exactly as registered in the platform |
| Re-instructing automatic behaviors | Clutters the instructions, can conflict with platform defaults | Don't write instructions for memory, error handling, response format, or confidentiality — see Section 8 |
| Using tool data in a later step without "remember" | Data is lost after the customer replies — agent fabricates or re-asks | Add "Remember the X" after tool calls whose data is needed in future steps — see Section 3 |
| Using `{{variable}}` in a response without saving it | Agent has no value to fill in — blanks or fabricated data | Always "Remember X as {{variable}}" before referencing it in a response |
| Checking a field the tool doesn't return | Condition never matches — agent guesses wrong | Only check data the tool actually returns — see the tool guide for output fields |

---
*Yellow.ai Builder Platform — Internal Guide v0.3*
