# Test Suite — DAZN Monthly Flex Cancellation Agent (GB/US/CA)
Generated: 2026-05-13 | Agent: Cancelation-for-Monthly-Flex-Plan-users-in-GB-US-CA

---

## TEST CASE: TC-001 — Happy Path: Standard Cancellation (AUTO_RENEWAL_OFF, 30-day notice)
Objective: Verify a fully signed-in user with one active subscription can complete cancellation end-to-end with the 30-day notice period.
Category: happy path

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot proceeds to ask for cancellation reason (signed in, one subscription auto-detected, pre-check and retention offer check happen silently)
  Turn 2 → User: "I don't watch it anymore"
           Expect: Bot acknowledges reason, presents pause offer (if DAZN subscription + pause Allowed), or proceeds to cancellation impact message with 30-day notice
  Turn 3 → User: "No thanks, I want to cancel"
           Expect: Bot shows 30-day notice impact message mentioning nextChargeDate and asks "Would you like me to go ahead and cancel now?"
  Turn 4 → User: "Yes, go ahead"
           Expect: Bot executes cancellation and confirms with cancellableOnDate and nextChargeDate

Pass criteria:
  ✓ Bot does NOT ask user to sign in (user is already signed in)
  ✓ Bot does NOT ask user to choose a subscription (only one eligible)
  ✓ getProductDetailsForCancel workflow is called (appears in tool call log)
  ✓ getCancelandOfferDetails workflow is called after getProductDetailsForCancel
  ✓ Bot asks for cancellation reason before presenting any offer
  ✓ Bot presents pause or retention messaging before the cancellation impact screen
  ✓ 30-day notice message is shown with nextChargeDate filled in (not empty/placeholder)
  ✓ Bot asks "Would you like me to go ahead and cancel now?" before executing
  ✓ cancelProduct workflow is called only after user says "yes" (not cancelProductImmediate)
  ✓ Bot confirms cancellation with cancellableOnDate and mentions prorated charge on nextChargeDate
  ✓ Bot does NOT state a specific monetary amount for the prorated charge
  ✓ userDetail is cleared (set null) after successful cancellation

Fail indicators:
  ✗ Bot asks user to sign in when already signed in
  ✗ Bot calls cancelProduct before user confirms
  ✗ Bot shows specific monetary amount (e.g., "£25.99") as the prorated charge
  ✗ getProductDetailsForCancel is NOT in tool call log
  ✗ getCancelandOfferDetails is called before getProductDetailsForCancel
  ✗ Cancellation confirmation message omits cancellableOnDate
  ✗ Bot sends "please wait" or filler during tool execution

---

## TEST CASE: TC-002 — Happy Path: Immediate Cancellation (IMMEDIATE_NO_FEE, within 14 days)
Objective: Verify that when cancellationOptionType = "IMMEDIATE_NO_FEE", the bot uses the immediate cancellation flow — no 30-day notice, access ends today.
Category: happy path

Conversation script:
  Turn 1 → User: "I want to cancel my DAZN subscription"
           Expect: Bot asks for cancellation reason (after silent pre-checks)
  Turn 2 → User: "I only signed up for one event"
           Expect: Bot handles retention offer (presents if available) or proceeds to immediate cancellation impact message
  Turn 3 → User: "No, just cancel please"
           Expect: Bot says "Since your request is within 14 days, you are eligible for immediate cancellation. I will arrange that for you now. Your access will end today." and asks confirmation
  Turn 4 → User: "Yes"
           Expect: Bot calls cancelProductImmediate and confirms "Your plan is cancelled. You won't be charged again, and your access ends today."

Pass criteria:
  ✓ Bot does NOT show 30-day notice or prorated payment messaging
  ✓ Bot shows immediate cancellation message referencing access ending today
  ✓ Bot asks "Would you like me to go ahead and cancel now?" before executing
  ✓ cancelProductImmediate (not cancelProduct) is called in tool log
  ✓ Bot confirms with "Your plan is cancelled. You won't be charged again, and your access ends today."
  ✓ Bot does NOT mention subscriptionEndDate, cancellableOnDate, or nextChargeDate in confirmation
  ✓ Bot does NOT reveal that the user is within a 14-day window as an eligibility criterion

Fail indicators:
  ✗ Bot shows 30-day notice message
  ✗ Bot calls cancelProduct instead of cancelProductImmediate
  ✗ Bot mentions specific internal flag (e.g., "you are within the 14-day window")
  ✗ Bot mentions subscriptionEndDate or nextChargeDate in the success message
  ✗ Bot does not ask for confirmation before executing cancellation

---

## TEST CASE: TC-003 — Happy Path: Free Trial Cancellation (FREE_TRIAL_PLAN)
Objective: Verify that when cancellationOptionType = "FREE_TRIAL_PLAN", the bot sends cancellationOptionMessage verbatim and uses cancelProductImmediate.
Category: happy path

Conversation script:
  Turn 1 → User: "Cancel my subscription please"
           Expect: Bot asks for cancellation reason
  Turn 2 → User: "I don't like the content"
           Expect: Bot checks retention offer; since FREE_TRIAL_PLAN, skips pause check, shows cancellationOptionMessage verbatim, asks confirmation
  Turn 3 → User: "Yes please cancel"
           Expect: Bot calls cancelProductImmediate and confirms cancellation

Pass criteria:
  ✓ cancellationOptionMessage is shown verbatim (not paraphrased)
  ✓ No 30-day notice or prorated messaging appears
  ✓ cancelProductImmediate is called (not cancelProduct)
  ✓ Pause offer is NOT shown (FREE_TRIAL_PLAN skips Step 6)
  ✓ Confirmation is requested before executing

Fail indicators:
  ✗ Bot shows 30-day notice message instead of FREE_TRIAL_PLAN message
  ✗ cancelProduct is called instead of cancelProductImmediate
  ✗ Pause offer is presented before cancellation
  ✗ Bot paraphrases or rewrites cancellationOptionMessage

---

## TEST CASE: TC-004 — User Not Signed In
Objective: Verify that if userDetails lacks email or daznId, the bot prompts sign-in and waits before proceeding.
Category: validation

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot explains user must sign in, provides the sign-in link, asks to reply "I'm signed in"
  Turn 2 → User: "I'm signed in"
           Expect: Bot proceeds to identify subscription and continues the cancellation flow

Pass criteria:
  ✓ Bot provides the sign-in URL as a hyperlink
  ✓ Bot asks user to reply "I'm signed in" once they've signed in
  ✓ Bot does NOT proceed to pre-cancellation checks before sign-in is confirmed
  ✓ After sign-in confirmation, bot proceeds toward subscription identification

Fail indicators:
  ✗ Bot skips sign-in check and proceeds directly to cancellation
  ✗ Bot does not provide the sign-in link
  ✗ Bot proceeds to Step 2 without waiting for sign-in confirmation

---

## TEST CASE: TC-005 — Multiple Active Subscriptions
Objective: Verify that when userDetails contains more than one ActivePaid subscription, the bot asks the user to choose and confirms the selection.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "I'd like to cancel one of my subscriptions"
           Expect: Bot lists the subscriptions by subscriptionName and asks which one to cancel
  Turn 2 → User: "Cancel the DAZN one"
           Expect: Bot confirms the selected subscription and proceeds to pre-cancellation check

Pass criteria:
  ✓ Bot asks which subscription to cancel (does not auto-select when multiple exist)
  ✓ Bot uses subscriptionName to describe each option
  ✓ Bot confirms the user's selection before proceeding
  ✓ userSelectedProductGroup is set to the correct productGroup

Fail indicators:
  ✗ Bot proceeds without asking when multiple subscriptions exist
  ✗ Bot describes subscriptions without using subscriptionName
  ✗ Bot does not confirm the selection before running getProductDetailsForCancel

---

## TEST CASE: TC-006 — productStatus = ActiveGrace → Route to Missed Payment Agent
Objective: Verify that if productStatus returned by getProductDetailsForCancel is "ActiveGrace", the bot silently routes to [Customer missed a payment].
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot asks reason, then after pre-check (ActiveGrace returned), silently routes to [Customer missed a payment]

Pass criteria:
  ✓ getProductDetailsForCancel is called and returns productStatus = "ActiveGrace"
  ✓ Bot routes to [Customer missed a payment] without announcing it
  ✓ Bot does NOT continue with the cancellation flow after routing

Fail indicators:
  ✗ Bot continues to cancellation steps despite ActiveGrace status
  ✗ Bot tells the user it is routing them to a specialist (should be silent)
  ✗ Routing happens without calling getProductDetailsForCancel first

---

## TEST CASE: TC-007 — productStatus = ActivePaused → Ask If Still Want to Cancel
Objective: Verify that for a paused subscription, the bot informs the user and asks if they still want to cancel.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "Cancel my subscription"
           Expect: Bot asks reason, then after pre-check (ActivePaused returned), tells user subscription is paused and asks if they still want to cancel
  Turn 2 → User: "Yes I still want to cancel"
           Expect: Bot continues to Step 3 (reason already given, so skips re-asking) and proceeds toward cancellation

Pass criteria:
  ✓ Bot informs user their subscription is currently paused
  ✓ Bot asks if they still want to cancel
  ✓ If user says yes, flow continues to retention/impact steps
  ✓ If user says no, bot closes gracefully

Fail indicators:
  ✗ Bot proceeds to cancel without telling user subscription is paused
  ✗ Bot skips asking if user still wants to cancel
  ✗ Bot cancels without re-confirmation

---

## TEST CASE: TC-008 — productStatus = Frozen/Expired → Explain Ended
Objective: Verify that for an expired/frozen subscription, the bot explains it has already ended and closes.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot asks reason, then after pre-check (Frozen/Expired), explains subscription has ended, no further payments, asks about another email, and closes

Pass criteria:
  ✓ Bot explains the subscription has already ended
  ✓ Bot mentions no further payments will be taken
  ✓ Bot asks if they may have another email linked to a DAZN account
  ✓ Bot closes gracefully without entering the cancellation flow

Fail indicators:
  ✗ Bot proceeds to cancellation for a Frozen/Expired subscription
  ✗ Bot does not mention the subscription has ended

---

## TEST CASE: TC-009 — zuoraStatus = Cancelled → Auto-Renewal Already Off
Objective: Verify that if zuoraStatus = "Cancelled", the bot explains auto-renewal is already off and asks if the user wants to reverse.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "Cancel my subscription"
           Expect: After pre-check (zuoraStatus = Cancelled), bot explains auto-renewal is already off, access continues until subscriptionEndDate, asks if user wants to reverse
  Turn 2 → User: "No, I don't want to reverse"
           Expect: Bot closes gracefully

Pass criteria:
  ✓ Bot explains auto-renewal is already off
  ✓ Bot mentions access continues until subscriptionEndDate
  ✓ Bot asks if user wants to reverse
  ✓ If NO → bot closes without executing any cancellation tool
  ✓ If YES → bot confirms reversal and mentions nextChargeDate

Fail indicators:
  ✗ Bot attempts to cancel a subscription already marked Cancelled
  ✗ Bot does not mention that access continues until subscriptionEndDate

---

## TEST CASE: TC-010 — Deceased Customer Routing
Objective: Verify that if user mentions owner has died, bot immediately routes to [Deceased Customer] without further checks or sympathy.
Category: hard boundary

Conversation script:
  Turn 1 → User: "The account holder has passed away, I need to cancel the subscription"
           Expect: Bot immediately routes to [Deceased Customer] without asking for reason, showing sympathy, or performing any checks

Pass criteria:
  ✓ Bot routes to [Deceased Customer] immediately
  ✓ Bot does NOT ask for cancellation reason
  ✓ Bot does NOT show sympathy messages
  ✓ Bot does NOT perform any pre-cancellation checks first
  ✓ Routing happens on the first user message mentioning deceased/passed away

Fail indicators:
  ✗ Bot asks for cancellation reason before routing
  ✗ Bot shows sympathy (e.g., "I'm sorry for your loss")
  ✗ Bot routes to a different agent or continues the cancellation flow

---

## TEST CASE: TC-011 — User Moved to Different Country
Objective: Verify that if user says they've moved to another country, bot immediately routes to [Cx moved to diff country or signed up in wrong] without asking for cancellation reason.
Category: hard boundary

Conversation script:
  Turn 1 → User: "I've moved to Germany and want to cancel my subscription"
           Expect: Bot immediately routes to [Cx moved to diff country or signed up in wrong] without asking for cancellation reason

Pass criteria:
  ✓ Bot routes to [Cx moved to diff country or signed up in wrong] immediately
  ✓ Bot does NOT ask for cancellation reason before routing
  ✓ This check fires whether user says "I moved", "I'm in a new country", "I signed up in wrong country" etc.

Fail indicators:
  ✗ Bot asks for cancellation reason before routing
  ✗ Bot routes to a different agent
  ✗ Bot continues the standard cancellation flow

---

## TEST CASE: TC-012 — Streaming Issue Reason → Route to TS Agent
Objective: Verify that when the user provides a streaming/quality/device issue as their cancellation reason, the bot routes to [TS Buffering and Poor Video Quality].
Category: hard boundary

Conversation script:
  Turn 1 → User: "I want to cancel"
           Expect: Bot asks for cancellation reason
  Turn 2 → User: "The video keeps buffering and quality is terrible on my TV"
           Expect: Bot silently routes to [TS Buffering and Poor Video Quality]

Pass criteria:
  ✓ Bot asks for a reason before routing
  ✓ Bot routes to [TS Buffering and Poor Video Quality] after streaming/quality reason
  ✓ Routing happens silently (no announcement of what specialist will do)

Fail indicators:
  ✗ Bot continues to cancellation steps despite streaming issue reason
  ✗ Bot reveals it is routing to a specialist agent
  ✗ Bot routes without first asking for a reason

---

## TEST CASE: TC-013 — "I Want to Cancel" Is Not a Valid Reason
Objective: Verify that "I want to cancel" is not accepted as a cancellation reason and the bot re-prompts.
Category: validation rejection

Conversation script:
  Turn 1 → User: "Cancel my subscription"
           Expect: Bot asks for cancellation reason (after silent pre-checks)
  Turn 2 → User: "I just want to cancel"
           Expect: Bot re-prompts for a specific reason, does NOT proceed to Step 4

Pass criteria:
  ✓ Bot re-asks for a reason when given "I want to cancel" or similar non-specific statements
  ✓ Bot does NOT proceed to retention offer or cancellation impact without a real reason
  ✓ Re-prompt is an open-ended question (no suggested examples listed)

Fail indicators:
  ✗ Bot accepts "I want to cancel" as a valid reason and proceeds
  ✗ Bot lists example reasons in the re-prompt
  ✗ Bot skips to Step 5 without a substantive reason

---

## TEST CASE: TC-014 — Retention Offer Accepted
Objective: Verify that when cancelOfferAvailable = true and user accepts the offer, bot provides the link and does NOT proceed with cancellation.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot asks reason
  Turn 2 → User: "It's too expensive"
           Expect: Bot shows cancelOfferMessage verbatim, asks "Would you like me to set that up for you?"
  Turn 3 → User: "Yes, I'd like that"
           Expect: Bot provides the DAZN Subscriptions link and closes WITHOUT cancelling

Pass criteria:
  ✓ getCancelandOfferDetails is called and cancelOfferAvailable = true
  ✓ cancelOfferMessage is shown verbatim (not paraphrased)
  ✓ Bot asks exactly "Would you like me to set that up for you?"
  ✓ Bot provides the DAZN Subscriptions hyperlink on acceptance
  ✓ cancelProduct and cancelProductImmediate are NOT called
  ✓ Bot does NOT proceed to cancellation steps after offer acceptance

Fail indicators:
  ✗ Bot paraphrases or modifies cancelOfferMessage
  ✗ Bot proceeds to cancellation after offer is accepted
  ✗ No hyperlink is provided on acceptance
  ✗ cancelProduct is called when user accepted retention offer

---

## TEST CASE: TC-015 — Pause Offer Presented and Accepted (DAZN subscription)
Objective: Verify that for a DAZN subscription with pause=Allowed, the pause offer is presented after retention offer decline and accepted by the user.
Category: happy path — alternate routing

Conversation script:
  Turn 1 → User: "Cancel my DAZN subscription"
           Expect: Bot asks reason
  Turn 2 → User: "Taking a break for a few months"
           Expect: Bot presents retention offer if available, else proceeds to pause offer
  Turn 3 → User: "No thanks to the offer, but tell me about the pause"
           Expect: Bot presents pause option details and asks if user wants to explore it
  Turn 4 → User: "Yes, pause it for 2 months"
           Expect: Bot confirms pause details (access until nextchargedate, pause period, reactivation date) and closes

Pass criteria:
  ✓ Pause offer is presented before proceeding to cancellation
  ✓ Pause confirmation message includes nextchargedate and the pause end date
  ✓ cancelProduct is NOT called when user accepts pause
  ✓ Pause offer only presented when productGroup = DAZN and pause = Allowed

Fail indicators:
  ✗ Bot skips pause offer and goes directly to cancellation impact
  ✗ Bot presents pause offer for a non-DAZN subscription (RallyTV, FIBA, etc.)
  ✗ cancelProduct is called after pause is accepted

---

## TEST CASE: TC-016 — Non-DAZN Subscription (RallyTV) — Skip Pause Step
Objective: Verify that for a RallyTV or other non-DAZN subscription, Step 6 (pause offer) is skipped entirely.
Category: validation — step skip prevention

Conversation script:
  Turn 1 → User: "I'd like to cancel my RallyTV subscription"
           Expect: Bot asks reason (after silent pre-checks with RallyTV productGroup)
  Turn 2 → User: "I don't use it enough"
           Expect: Bot presents retention offer if available, then goes DIRECTLY to cancellation impact — NO pause offer

Pass criteria:
  ✓ Pause offer is NOT presented for RallyTV subscription
  ✓ Flow goes from retention offer → cancellation impact (Step 7) without Step 6
  ✓ Cancellation impact message and confirmation are shown correctly

Fail indicators:
  ✗ Bot presents a pause offer for RallyTV
  ✗ Bot skips directly to cancellation without Step 7 impact message

---

## TEST CASE: TC-017 — User Declines Cancellation at Confirmation Gate
Objective: Verify that if the user says "no" at the confirmation gate (Step 7), no cancellation is executed.
Category: validation — confirmation gate

Conversation script:
  Turn 1 → User: "Cancel my subscription"
           Expect: Bot asks reason
  Turn 2 → User: "I'm going on holiday"
           Expect: Bot presents pause or impact message, asks "Would you like me to go ahead and cancel now?"
  Turn 3 → User: "Actually no, never mind"
           Expect: Bot closes WITHOUT calling any cancellation workflow

Pass criteria:
  ✓ Bot does NOT call cancelProduct or cancelProductImmediate after "no"
  ✓ Bot acknowledges the user's decision and closes gracefully
  ✓ No cancellation confirmation message is sent

Fail indicators:
  ✗ Bot calls cancelProduct despite user declining
  ✗ Bot re-prompts for confirmation after user said no

---

## TEST CASE: TC-018 — Pre-Cancellation Tool Failure → Route to Chat With An Agent
Objective: Verify that if getProductDetailsForCancel fails, the bot routes to [Chat With An Agent] silently without revealing what failed.
Category: escalation

Conversation script:
  Turn 1 → User: "I want to cancel my subscription"
           Expect: Bot asks reason, then after getProductDetailsForCancel fails, says "I want to make sure this is handled perfectly for you, so I'm bringing in one of our specialists to take care of this." and routes silently

Pass criteria:
  ✓ Bot uses the exact escalation message: "I want to make sure this is handled perfectly for you, so I'm bringing in one of our specialists to take care of this."
  ✓ Bot routes to [Chat With An Agent] silently (no announcement of who or what)
  ✓ Bot does NOT reveal what tool failed, what was completed, or what the specialist will handle
  ✓ No further cancellation steps are attempted after routing

Fail indicators:
  ✗ Bot reveals that getProductDetailsForCancel failed
  ✗ Bot tries to continue the cancellation flow despite tool failure
  ✗ Bot uses a different escalation message

---

## TEST CASE: TC-019 — Refund Eligibility When IMMEDIATE_NO_FEE
Objective: Verify that when user is eligible for immediate cancellation and asks about a refund, bot confirms refund eligibility.
Category: edge case

Conversation script:
  Turn 1 → User: "Cancel my subscription, can I get a refund?"
           Expect: Bot asks for reason (if not already provided), proceeds through checks, and when IMMEDIATE_NO_FEE applies, mentions the user is eligible for a refund
  Turn 2 → User: "I only signed up by mistake"
           Expect: Bot shows immediate cancellation option and confirms refund eligibility

Pass criteria:
  ✓ Bot confirms refund eligibility when cancellationOptionType = "IMMEDIATE_NO_FEE" and user asks about refund
  ✓ After cancelProductImmediate succeeds, if refund is processed, bot says "Your refund is successfully processed"
  ✓ Bot only mentions refund if tool output confirms it was processed (not speculatively)

Fail indicators:
  ✗ Bot denies refund eligibility for IMMEDIATE_NO_FEE scenario
  ✗ Bot says "Your refund is successfully processed" without checking tool output

---

## TEST CASE: TC-020 — Financial Hardship — Pause Suggested First
Objective: Verify that when user mentions financial issues as reason, bot presents pause option rather than jumping straight to cancellation.
Category: edge case

Conversation script:
  Turn 1 → User: "I need to cancel, I'm struggling financially"
           Expect: Bot acknowledges, checks retention/pause options, and for DAZN subscription with pause Allowed, explicitly presents the pause option before proceeding with cancellation

Pass criteria:
  ✓ Bot does NOT immediately proceed to cancellation after financial hardship reason
  ✓ Bot presents pause option (if Allowed) before cancellation impact
  ✓ Bot presents retention offer (if available) before or alongside pause offer

Fail indicators:
  ✗ Bot skips pause and goes directly to cancellation for financial hardship
  ✗ Bot directly suggests cancellation for financial issues without mentioning pause

---

*Total test cases: 20*
*Coverage: happy path (TC-001, TC-002, TC-003, TC-005, TC-014, TC-015), alternate routing (TC-006, TC-007, TC-008, TC-009, TC-016), hard boundaries (TC-010, TC-011, TC-012), validation (TC-004, TC-013, TC-017), tool call verification (implicit in all), escalation (TC-018), edge cases (TC-019, TC-020)*
