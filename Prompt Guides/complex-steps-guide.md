# Writing Complex Step-Based Agents
**Yellow.ai Builder Platform — Advanced Guide**

## Contents

- [1. When to Use Steps](#1-when-to-use-steps)
- [2. Step Anatomy](#2-step-anatomy)
- [3. Branching](#3-branching)
  - [Simple if/otherwise](#simple-ifotherwise)
  - [Multi-way branching](#multi-way-branching)
  - [Nested conditions](#nested-conditions)
  - [Conditional with data checks](#conditional-with-data-checks)
- [4. Using Data Across Steps](#4-using-data-across-steps)
  - [Tool output → condition (same step)](#tool-output--condition-same-step)
  - [Tool output → later step (remember)](#tool-output--later-step-remember)
  - [Customer input → condition](#customer-input--condition)
  - [Customer input → later step (remember)](#customer-input--later-step-remember)
  - [Combining data sources in conditions](#combining-data-sources-in-conditions)
  - [Using data in responses](#using-data-in-responses)
- [5. Skipping Steps](#5-skipping-steps)
- [6. Looping Back](#6-looping-back)
- [7. Parallel Data Gathering](#7-parallel-data-gathering)
- [8. Sub-Flows Within a Step](#8-sub-flows-within-a-step)
- [9. Error Paths and Fallbacks](#9-error-paths-and-fallbacks)
- [10. Handoff Between Agents](#10-handoff-between-agents)
- [11. Patterns for Common Scenarios](#11-patterns-for-common-scenarios)
  - [11A. Retry Loop with Max Attempts](#11a-retry-loop-with-max-attempts)
  - [11B. Verification Gate](#11b-verification-gate)
  - [11C. Multi-Item Processing](#11c-multi-item-processing)
  - [11D. Escalation Ladder](#11d-escalation-ladder)
- [12. Full Complex Example](#12-full-complex-example)
- [13. Step Design Rules](#13-step-design-rules)
- [14. Common Mistakes in Complex Flows](#14-common-mistakes-in-complex-flows)


# 1. When to Use Steps

Use numbered steps when your agent has **any** of the following:

- More than two decision points
- Loops (retry, go back to a previous step)
- Conditions that depend on tool results
- Multiple possible outcomes (resolved, escalated, abandoned)
- Steps that must happen in a strict order

If the flow is linear with one or two branches, plain prose is simpler. If you're unsure, start with prose — you can always restructure into steps later.

**Rule of thumb:** if you draw the flow on paper and it has more than one arrow going backward or more than three forward branches, use steps.


# 2. Step Anatomy

Every step follows this structure:

```
## Step N — [Descriptive Name]

[What to do — one action or one question]

[Example response in quotes — if this step speaks to the customer]

[Branches — what happens next, based on outcome]
```

**Rules:**
- One action per step. "Greet AND verify AND check status" is three steps.
- Every step has a name, not just a number.
- Every step that asks a question defines what to do with the answer.
- Every step ends with a clear "next" — either a branch, a `→ Go to Step N`, or it's the final close step.


# 3. Branching

Branching is the most important part of complex agents. A branch is a decision point where the flow can go in different directions.

## Simple if/otherwise

The most basic branch — two paths.

```
## Step 3 — Check account status

Check whether the account is blocked.

If the account is blocked → Go to Step 4
Otherwise → Go to Step 5
```

**Always include the "otherwise" path.** Without it, the agent doesn't know what to do for the non-matching case.

## Multi-way branching

When there are more than two possible outcomes.

```
## Step 4 — Determine issue type

Ask the customer what issue they are experiencing.

  "Could you describe what's happening? For example,
  are you seeing an error message, a blank screen,
  or something else?"

Wait for reply.

If they report an error message → Go to Step 5
If they report a blank screen → Go to Step 6
If they report slow performance → Go to Step 7
If their issue doesn't match any of the above → Go to Step 8
```

**The last branch must be a catch-all.** "If none of the above" or "If their issue doesn't match" prevents the agent from getting stuck.

## Nested conditions

When a branch depends on multiple things at once.

```
## Step 5 — Check eligibility

Check the customer's subscription status and region.

If subscription is active AND region is US or UK → Go to Step 6
If subscription is active AND region is elsewhere → Go to Step 7
If subscription is expired → Go to Step 8
If subscription is cancelled → Go to Step 9
```

**Keep nesting to two levels maximum.** If you need three or more conditions combined, split into separate steps:

```
✗  If active AND US AND premium AND not blocked → Step 6

✓  ## Step 5a — Check subscription
   If active → Go to Step 5b
   If expired or cancelled → Go to Step 8

   ## Step 5b — Check region
   If US or UK → Go to Step 5c
   Otherwise → Go to Step 7

   ## Step 5c — Check plan type
   If premium → Go to Step 6
   Otherwise → Go to Step 6 (same flow, but note the plan)
```

## Conditional with data checks

When the branch depends on data from a tool call or memory.

```
## Step 3 — Look up order

Fetch the order using [fetch-order-details].

If the order is found and status is "delivered" → Go to Step 4
If the order is found and status is "in transit" → Go to Step 5
If the order is found and status is "cancelled" → Go to Step 6
If no order is found → Go to Step 7
If the tool call fails → Go to Step 8
```

**Always include "not found" and "tool failure" branches for tool calls.** Tools can fail. Data can be missing.


# 4. Using Data Across Steps

In complex agents, data flows between steps — tool results feed into conditions, customer answers get saved for later, and remembered values appear in responses. Getting this right is what separates a working agent from one that loses track or fabricates data.

## Tool output → condition (same step)

The most common pattern. Call a tool, then branch based on what it returns.

```
## Step 3 — Check account status

Fetch the account using [fetch-account-details].

If the account is found and active → Go to Step 4
If the account is found but blocked → Go to Step 5
If the account is found but subscription is expired → Go to Step 6
If no account is found → Go to Step 7
If the tool call fails → Go to Step 8
```

**How it works:** the tool returns data (e.g., `{ status: "active", is_blocked: false }`). The agent reads this result and evaluates your conditions against it. You write the conditions in plain English — the agent matches them to the tool output.

**Rules:**
- Only check fields the tool actually returns. If unsure, check the tool's output schema.
- Always include "not found" and "tool failure" branches.
- You don't need to reference field names like `is_blocked` — just write "if the account is blocked."

## Tool output → later step (remember)

When a tool result is needed in a step that happens **after a customer reply** (a different turn), you must save it.

```
## Step 2 — Look up billing history

Fetch billing history using [fetch-billing-history].
Remember the billing history — you'll need it to
confirm the charge in Step 4 and for the refund in Step 6.

→ Go to Step 3


## Step 3 — Ask about the charge

  "I can see several recent charges on your account.
  Which one are you asking about?"

Wait for reply.

→ Go to Step 4


## Step 4 — Confirm the charge

Match what the customer described against the
billing history you saved in Step 2.

If a matching charge is found:
  "I found a charge of {{charge_amount}} on
  {{charge_date}}. Is that the one?"
  Wait for reply.
  If confirmed → Go to Step 5
  If not → ask them to clarify → re-check

If no matching charge:
  "I'm not seeing a charge matching that description.
  Could you check the amount or date?"
  Wait for reply. → Re-check.
```

**Why "remember" matters here:** Between Step 2 and Step 4, the customer sends a reply (Step 3). That reply starts a new turn. Without "remember", the billing data from the tool call is no longer available.

**Use `{{ }}` to name specific fields you'll need in responses:**

```
Remember the charge amount as {{charge_amount}}
and the charge date as {{charge_date}}.
```

## Customer input → condition

After the customer replies, branch based on what they said.

```
## Step 5 — Ask about device

  "What device are you trying to use — phone, tablet,
  computer, or TV?"

Wait for reply.

If phone or tablet → Go to Step 6 (Mobile troubleshooting)
If computer → Go to Step 7 (Desktop troubleshooting)
If TV → Go to Step 8 (TV troubleshooting)
If unclear or they mention multiple devices →
  "Just to make sure I give you the right steps,
  which single device are you having the most
  trouble with?"
  Wait for reply. → Re-evaluate.
```

**Rules:**
- Be flexible in your conditions. Customers say "my phone", "iPhone", "mobile" — these all mean "phone."
- Always include a catch-all for unclear answers.
- If the customer's answer doesn't match any branch, don't guess — ask again.

## Customer input → later step (remember)

When the customer provides information that's needed later — especially for escalation or closing summaries.

```
## Step 5 — Gather information

  "Before we troubleshoot, I need a few details:
  when did the issue start, what error are you
  seeing, and what device are you using?"

Wait for reply.

Remember the error message, start date, and device type.
You'll need these if you escalate in Step 9.

→ Go to Step 6


...


## Step 9 — Escalate

Send to [tier-2-technical] with:
- The error message from Step 5
- The device type from Step 5
- The start date from Step 5
- The troubleshooting steps already attempted (from Step 6-8)
```

## Combining data sources in conditions

Sometimes a condition depends on data from **multiple sources** — a tool result AND customer input, or tool data AND remembered data.

```
## Step 6 — Determine next action

The customer has confirmed they want a refund.
Check the refund amount from the billing history
you saved earlier.

If the refund amount is under £50 AND the charge
is within the refund window → Go to Step 7 (Process refund)

If the refund amount is £50 or over → Go to Step 8
(Escalate — manual approval required)

If the charge is outside the refund window →
  Check [kb: refund exceptions].
  If an exception applies → Go to Step 7
  Otherwise → Go to Step 9 (Cannot refund)
```

**Pattern:** tool data (billing history) + KB data (refund policy) + business rule (£50 limit) all feed into one decision point. Write each condition clearly, combining with AND when needed.

## Using data in responses

When building a response that includes data, use `{{ }}` for values you saved to memory, and natural descriptions for data the agent just retrieved.

**Using saved/remembered data (from memory):**
```
"Your refund of {{charge_amount}} has been processed
and should appear within 3-5 business days."
```

**Using data just retrieved (from a tool call in the same step):**
```
Fetch delivery status using [check-delivery-status].

If delivered:
  "Our records show your package was delivered on
  [the delivery date from the tool result]. Could
  you check with your building reception?"
```

For tool data you'll reference in a response, it's cleaner to save it first:

```
Fetch delivery status using [check-delivery-status].
Remember the delivery date as {{delivery_date}}.

If delivered:
  "Our records show your package was delivered on
  {{delivery_date}}. Could you check with your
  building reception?"
```

**Rule of thumb:** if a value appears in a quoted example response, save it with a `{{ }}` name first. This makes the response template clear and ensures the data is available.


# 5. Skipping Steps

Sometimes a step is not needed based on earlier information. Use skip logic to jump ahead.

```
## Step 2 — Verify identity

If the customer was already verified in a previous
interaction (check ongoing context) → Skip to Step 4

If verification is needed:
  Ask the customer to confirm their email address.

  Wait for reply.

  If confirmed → Go to Step 3
  If they cannot confirm → Go to Step 10 (Close — unverified)
```

**Rules for skipping:**
- Always explain the condition for the skip.
- Skip forward only — never skip backward (that's a loop, covered below).
- "Skip to Step N" means you are marking the skipped steps as not needed.
- If the data that would be collected in skipped steps is needed later, make sure it's already available in memory.


# 6. Looping Back

Sometimes the agent needs to go back to a previous step — for retries, corrections, or when the customer provides new information.

```
## Step 3 — Confirm email

Ask the customer to confirm their email address.

  "Could you confirm the email address on your account?"

Wait for reply.

If the email matches the account → Go to Step 4
If the email doesn't match:
  "That email doesn't match what we have on file.
  Could you try another email you might have used?"

  Wait for reply.

  If they provide a new email → remember the new email
    and go back to Step 2 (Look up account with new email)
  If they're unsure or want to stop →
    Go to Step 9 (Close — unable to verify)
```

**Rules for loops:**
- Always limit the number of times a loop can execute. If you don't, the conversation can go forever.
- State the limit explicitly: "If this is the third attempt, stop and escalate."
- When looping back, tell the agent what new data to use: "go back to Step 2 **with the new email**."

**Retry pattern with a limit:**

```
## Step 6 — Attempt password reset

Send a password reset using [send-password-reset].

If successful → Go to Step 7
If it fails:
  "There was an issue sending the reset email.
  Let me try once more."

  Retry [send-password-reset] once.

  If the retry succeeds → Go to Step 7
  If the retry also fails:
    "I'm sorry, I wasn't able to send the reset email.
    Let me connect you with our technical team."
    → Go to Step 9 (Escalate)
```


# 7. Parallel Data Gathering

When you need multiple pieces of data before continuing, fetch them together in BEFORE YOU START. Don't spread tool calls across steps unnecessarily.

```
BEFORE YOU START

Fetch the customer's account using [fetch-account-details].
Fetch their subscription using [fetch-subscription-details].
Fetch their billing history using [fetch-billing-history].

If any of these fail, retry once. If still failing after
retry, continue with whatever data you have — note what
is missing and mention it if relevant.

Remember all results — you'll need them throughout.
```

**Why BEFORE YOU START?** Tool calls in BEFORE YOU START happen before the agent speaks. This avoids awkward mid-conversation pauses like "Let me check... one moment... okay now let me check something else..."

If you need data that depends on what the customer says (e.g., an order ID they give you), that tool call belongs in a step.


# 8. Sub-Flows Within a Step

Some steps have internal work items that aren't separate steps — they're sub-tasks within one step. Use a bulleted list for these.

```
## Step 6 — Troubleshoot connectivity

Work through the following with the customer, one at
a time. Move to the next item only after the customer
has tried the current one.

- Ask them to restart their device.
- Ask them to check their internet connection.
- Ask them to try a different browser.
- Ask them to clear cookies and cache.
- Ask them to try incognito/private mode.

After each item, ask if the issue is resolved.

If resolved at any point:
  "Great, glad that's working now!"
  → Go to Step 8 (Close)

If none of the items resolve the issue:
  "I've gone through all the standard steps and the
  issue is still there. Let me escalate this to our
  technical team."
  → Go to Step 7 (Escalate)
```

**Key rule:** sub-items within a step can span multiple conversation turns. "One step per turn" means one step header (`## Step N`), not one bullet point. The agent will ask one sub-item, wait for the reply, then move to the next.


# 9. Error Paths and Fallbacks

Every complex agent needs clear fallback paths. There are three types of failures:

**Tool failure** — the tool call returns an error.
```
Fetch the order using [fetch-order-details].

If the tool fails, retry once.
If it still fails:
  "I'm having trouble looking up your order right now.
  Could you try again in a few minutes, or I can
  connect you with a team member who can help."
  → Go to Step 7 (Escalate or Close)
```

**Data not found** — the tool succeeds but returns no data.
```
If no order is found for that ID:
  "I couldn't find an order with that ID. Could you
  double-check the order number? It should start
  with ORD- followed by 8 digits."

  Wait for reply.
  If they provide a corrected ID → retry the lookup
  If they can't find it → direct them to [kb: find order ID]
```

**Customer can't proceed** — the customer doesn't have the information needed.
```
If they cannot provide the required details:
  "No problem — you can find your order number in
  your confirmation email or on your account page
  at {{account_url}}. Feel free to contact us again
  once you have it."
  → Go to Step 8 (Close — incomplete)
```

**Every tool call needs at least a failure branch. Every question needs a "customer can't answer" branch.**


# 10. Handoff Between Agents

When your agent needs to transfer to another agent, pass context explicitly.

```
## Step 7 — Escalate to billing

  "I'll connect you with our billing team who can
  help with this further."

Send to [billing-agent] with:
- The customer's account ID
- The charge in question (amount and date)
- What has been discussed so far
- The reason the refund couldn't be processed here

→ Go to Step 8 (Close)
```

**Rules:**
- List what context to bring — don't just say "transfer."
- The receiving agent gets the full conversation history via handoff, but listing key data points ensures nothing is missed.
- Use the exact agent slug as registered in the platform.
- Don't announce the transfer mechanics to the customer — just say you're connecting them.


# 11. Patterns for Common Scenarios

## 11A. Retry Loop with Max Attempts

Use when you need the customer to provide valid input and want to limit attempts.

```
## Step 4 — Collect order ID

Ask the customer for their order ID.

  "Could you share your order ID? You'll find it in
  your confirmation email — it starts with ORD-."

Wait for reply.

Validate the format (should start with ORD- followed
by digits).

If valid → remember the order ID → Go to Step 5

If invalid:
  If this is the first or second attempt:
    "That doesn't look quite right — order IDs start
    with ORD- followed by 8 digits, like ORD-12345678.
    Could you check again?"
    Wait for reply. → Re-check at the top of this step.

  If this is the third attempt:
    "No worries — you can find your order ID at
    {{account_url}} or in your confirmation email.
    Feel free to come back once you have it."
    → Go to Step 9 (Close)
```

## 11B. Verification Gate

Use when a step must be passed before anything else can happen.

```
## Step 2 — Verify identity (gate)

This step is a gate — the customer cannot proceed
without passing verification.

Ask the customer to confirm their registered email
and the last 4 digits of their payment card.

  "For security, could you confirm the email address
  on your account and the last 4 digits of the card
  you used to sign up?"

Wait for reply.

Cross-check against account details from [fetch-account-details].

If both match → Go to Step 3
If email matches but card does not:
  "The card digits don't match what we have. Could
  you try the last 4 digits of another card you
  might have used?"
  Wait for reply. → Re-check.

If neither matches:
  "I wasn't able to verify your identity with those
  details. For security, I'm unable to proceed.
  Please try again or visit {{help_url}} for other
  ways to access your account."
  → Go to Step 8 (Close — unverified)
```

## 11C. Multi-Item Processing

Use when the customer may have multiple items to process (e.g., multiple returns, multiple charges).

```
## Step 5 — Process returns

For each item the customer wants to return:

1. Confirm the item name, order ID, and reason.
2. Check eligibility using [check-return-eligibility].
3. If eligible, process using [process-return].
4. If not eligible, explain why and move to the next item.

After each item, ask:
  "That one's done. Do you have another item to return?"

Wait for reply.

If yes → repeat this step for the next item.
If no → Go to Step 6 (Summary).

## Step 6 — Summary

Summarize all processed returns:
  "Here's a summary of what we processed today:
  [list each item, status, and expected refund timeline]

  Is everything correct?"

Wait for reply.

If confirmed → Go to Step 7 (Close)
If they want to change something → Go back to Step 5
```

## 11D. Escalation Ladder

Use when there are multiple levels of escalation depending on the situation.

```
## Step 7 — Determine escalation path

Based on what has happened so far, choose the
right escalation:

If the issue is technical (error codes, connectivity,
device-specific):
  Send to [tier-2-technical] with the error code,
  device type, and steps already attempted.

If the issue is billing-related (charges, refunds,
payment failures):
  Send to [billing-specialist] with the charge
  details and reason for escalation.

If the customer has requested a manager:
  Send to [manager-escalation] with a full summary
  of the conversation and the customer's concern.

If the issue doesn't fit any category:
  Send to [general-support] with a summary.

In all cases:
  "I'm connecting you with the right team now.
  They'll have all the details from our conversation."
  → Go to Step 8 (Close)
```


# 12. Full Complex Example

A complete agent with branching, skipping, looping, tool calls, and multiple close paths.

```
AGENT: Handle Delivery Issue

WHEN TO USE THIS
Use this when the customer has a problem with a delivery
— missing package, wrong item, damaged item, or delivery
to the wrong address.

Do not use this for order cancellations before shipment
— send those to [cancellation-agent].
Do not use this for refund requests unrelated to delivery
— send those to [refund-agent].
Do not use this for tracking questions where nothing is
wrong — send those to [order-tracking-agent].

GOAL
The customer's delivery issue is resolved — either by
re-sending, refunding, or escalating — and they know
what to expect next.

BEFORE YOU START
Fetch the customer's account using [fetch-account-details].
Fetch their recent orders using [fetch-recent-orders].

If either fails, retry once. If still failing, continue
and ask the customer for order details manually.

THE CONVERSATION

## Step 1 — Greet and confirm

Greet the customer and confirm they have a delivery issue.

  "Hi {{first_name}}, I understand there's an issue
  with a delivery. I'm here to help — could you tell
  me which order this is about?"

If you already have their recent orders, list the most
recent 2-3 so they can pick one.

Wait for reply.

→ Go to Step 2


## Step 2 — Identify the order

Match what the customer says to an order in their history.

If you can identify the order → remember the order ID
  and details → Go to Step 3

If you can't find a match:
  "I'm not seeing that order in your recent history.
  Could you share the order ID? You'll find it in
  your confirmation email."

  Wait for reply.

  If they provide an order ID → look it up with
    [fetch-order-details] → Go to Step 3
  If no order found after lookup:
    "I still can't find that order. Could you
    double-check the order number or the email
    address on the account?"

    Wait for reply.

    If they provide corrected info → retry lookup
    If they can't provide it →
      "No worries. You can check your order status at
      {{account_url}}. If the issue persists, come back
      with the order number and we'll sort it out."
      → Go to Step 9 (Close — unable to locate order)


## Step 3 — Identify issue type

Ask what the problem is with the delivery.

  "I found your order. What's the issue — is the
  package missing, was the wrong item delivered,
  or did it arrive damaged?"

Wait for reply.

If missing package → Go to Step 4
If wrong item → Go to Step 5
If damaged item → Go to Step 6
If delivered to wrong address → Go to Step 7
If something else → Go to Step 8


## Step 4 — Missing package

Check the delivery status with [check-delivery-status].

If status is "delivered" and delivery was less than
48 hours ago:
  "Our records show it was delivered on {{delivery_date}}.
  Sometimes packages take a day to appear — could you
  check with neighbours or your building reception?
  If it still hasn't turned up by tomorrow, let us
  know and we'll arrange a replacement."
  → Go to Step 9 (Close — monitoring)

If status is "delivered" and delivery was more than
48 hours ago:
  "It looks like this was marked as delivered over
  48 hours ago. I'll arrange a replacement for you."
  Process replacement using [create-replacement-order].
  → Go to Step 9 (Close — replacement sent)

If status is "in transit":
  "Your package is still in transit. The estimated
  delivery is {{estimated_date}}. Would you like to
  wait, or would you prefer a refund?"

  Wait for reply.

  If wait → Go to Step 9 (Close — waiting)
  If refund → Go to Step 6b (Process refund path)

If status is "stuck" or unknown:
  "There seems to be an issue with the delivery.
  Let me escalate this to our logistics team."
  Send to [logistics-team] with order ID, tracking
  info, and delivery status.
  → Go to Step 9 (Close — escalated)


## Step 5 — Wrong item

Ask the customer to confirm what they received vs.
what they ordered.

  "Sorry about that. Could you tell me what item you
  received? If you can share a photo, that would be
  really helpful."

Wait for reply. Remember what they tell you.

Check if the correct item is in stock using
[check-inventory].

If in stock:
  "I can send you the correct item right away. You
  don't need to return the wrong one — consider it
  on us. Does that work?"

  Wait for confirmation.

  If yes → process using [create-replacement-order]
    → Go to Step 9 (Close — replacement sent)
  If they want a refund instead → Go to Step 6b

If not in stock:
  "Unfortunately the correct item is out of stock
  right now. I can offer you a full refund instead.
  Would you like that?"

  Wait for reply.

  If yes → Go to Step 6b (Process refund path)
  If no — they want to wait for restock:
    "I'll set up a notification for when it's back.
    We'll reach out as soon as it's available."
    → Go to Step 9 (Close — waiting for restock)


## Step 6 — Damaged item

Ask for details and evidence.

  "Sorry to hear that. Could you describe the damage
  and share a photo if possible? This helps us process
  your claim faster."

Wait for reply. Remember the description and photo.

If they provide a photo and description:
  → Go to Step 6b (offer refund or replacement)

If they can't provide a photo:
  "No problem. Based on your description, I can still
  help. Would you prefer a replacement or a refund?"

  Wait for reply.

  If replacement → check inventory with [check-inventory]
    If in stock → process with [create-replacement-order]
      → Go to Step 9 (Close — replacement sent)
    If out of stock → offer refund → Go to Step 6b
  If refund → Go to Step 6b


## Step 6b — Process refund

Check refund eligibility using [check-refund-eligibility].

If eligible:
  Confirm with the customer:
  "I'll process a refund of {{order_amount}} to your
  original payment method. It should arrive in 3-5
  business days. Shall I go ahead?"

  Wait for confirmation.

  If confirmed → process using [process-refund]
    "Your refund has been processed. You should see
    {{order_amount}} back in your account within
    3-5 business days."
    → Go to Step 9 (Close — refund processed)

If not eligible:
  "Unfortunately this order falls outside our refund
  window. Let me check if there are other options."
  Check [kb: refund exceptions].
  If exception applies → process the refund as above.
  If no exception:
    "I'm not able to process a refund in this case,
    but I want to make sure you're taken care of.
    Let me connect you with a specialist."
    Send to [billing-specialist] with order details
    and refund denial reason.
    → Go to Step 9 (Close — escalated)


## Step 7 — Wrong address

Check whether the package has been delivered yet.

If not yet delivered:
  "The package hasn't been delivered yet. I can try
  to update the delivery address. What's the correct
  address?"

  Wait for reply.

  Attempt address update using [update-delivery-address].

  If successful:
    "Done — the delivery address has been updated to
    {{new_address}}. You should receive it as expected."
    → Go to Step 9 (Close — address updated)

  If the carrier doesn't support address changes:
    "Unfortunately the carrier can't change the address
    at this stage. Would you like me to arrange a
    replacement to the correct address once this one
    is returned?"

    Wait for reply. → Process accordingly.

If already delivered to wrong address:
  "It looks like the package was already delivered.
  Let me escalate this to our logistics team to
  investigate."
  Send to [logistics-team] with order details,
  intended address, and actual delivery address.
  → Go to Step 9 (Close — escalated)


## Step 8 — Other issue

The customer's issue doesn't match the standard categories.

  "I want to make sure I help you with the right thing.
  Could you give me a bit more detail about what
  happened with your delivery?"

Wait for reply.

If the issue becomes clear and matches a category
above → Go to the relevant step.

If the issue is unique or complex:
  "This sounds like something our specialist team
  can help with. Let me connect you."
  Send to [general-support] with a full summary
  of what the customer described.
  → Go to Step 9 (Close — escalated)

HOW TO CLOSE

## Step 9 — Close

  "Thanks for contacting {{brand_name}}, {{first_name}}.
  If anything else comes up, you can always reach us
  here or check {{help_url}} for quick answers.

  You'll receive a short survey after this chat —
  your feedback really helps us improve."

Add the appropriate tag based on resolution:
- Replacement sent: Auto_Assist_Delivery_Replacement
- Refund processed: Auto_Assist_Delivery_Refund
- Escalated: Auto_Assist_Delivery_Escalated
- Unable to resolve: Auto_Assist_Delivery_Unresolved

Mark the goal as completed.
```


# 13. Step Design Rules

These rules ensure your complex agent works reliably.

**1. One action per step.**
A step should do one thing: greet, ask, check, fetch, process.
If you're writing "greet the customer AND check their account AND verify identity", that's three steps.

**2. Every branch has a destination.**
Every `if` needs either a `→ Go to Step N` or a closing action. No dead ends.

```
✗  If the order is cancelled → (nothing)

✓  If the order is cancelled →
     "This order was already cancelled. Would you
     like help with something else?"
     → Go to Step 8 (Close)
```

**3. Every question has a catch-all.**
If you list three possible answers, add a fourth: "If none of the above" or "If their answer doesn't match."

**4. Tool calls have failure branches.**
Every `[tool-name]` call should be followed by success and failure paths.

**5. Loops have a maximum.**
If a step can loop back to itself or a previous step, state the maximum number of loops. "If this is the third attempt, escalate."

**6. Skip conditions reference available data.**
When you write "skip to Step 5 if already verified", the verification data must actually be available in memory. Don't skip over steps that collect data you need later.

**7. Close steps handle all outcomes.**
Your final step should account for every possible resolution: resolved, partially resolved, escalated, abandoned.

**8. Don't create hidden steps.**
Step 6b in the example above is a shared sub-routine called from multiple places. This is fine, but:
- Give it a clear name (Step 6b, not an unnamed paragraph).
- Make sure every path that calls it has a clear `→ Go to Step 6b`.
- It should still end with a clear destination.

**9. Use "Wait for reply" before every branch that depends on customer input.**
Without it, the agent may race ahead and answer its own question.

**10. Transfer steps pass context.**
When sending to another agent, list the specific data to bring — don't just say "transfer."


# 14. Common Mistakes in Complex Flows

| Mistake | What happens | Fix |
|---|---|---|
| Branch without destination | Agent gets stuck or invents a next step | Every branch needs `→ Go to Step N` |
| Loop without a limit | Conversation goes in circles forever | Add "if third attempt, escalate" |
| Skipping over data-collection steps | Later steps fail because data is missing | Check that skipped steps' data is already in memory |
| Two steps that both ask the customer a question | Customer gets asked twice in one turn | Merge into one step or ensure "Wait for reply" separates them |
| Complex conditions in one step | Agent misreads the condition or picks wrong branch | Split into smaller steps, max two conditions per branch |
| No catch-all branch | Agent doesn't know what to do with unexpected answers | Always add "if none of the above" |
| Step 6b-style sub-routine with no way back | Sub-routine is reached but has no `→ Go to Step N` at the end | Every sub-routine must end with a destination |
| Mixing tool calls and customer questions in one step | Agent tries to do both — either the tool call or the question gets dropped | One step fetches data, the next step asks the question |
| Not saying what went wrong in error branches | Customer gets a vague "something went wrong" | Be specific: "I couldn't find that order" not "there was an error" |
| Assuming tool data is always available | Agent proceeds without data and fabricates values | Check for data before using it, with a fallback if missing |
| Using tool data in a later step without "remember" | Agent doesn't have the data after the customer replies — it fabricates or asks again | Add "Remember the X" after every tool call whose data is needed in a future turn |
| Checking a field the tool doesn't return | Condition never matches or agent guesses | Only write conditions against data the tool actually returns — check the tool's output |
| Using `{{variable}}` in a response without saving it first | Agent has no value to fill in — response has blanks or fabricated data | Always "Remember X as {{variable}}" before using it in a quoted response |
| Writing conditions as code instead of English | Agent misinterprets technical syntax | Write "If the account is blocked" not "If is_blocked == true" |
| No condition for "data not found" | Agent assumes data always exists and fabricates when it doesn't | Always include "If no X is found" or "If the tool returns no results" |

---
*Yellow.ai Builder Platform — Advanced Guide v0.1*
