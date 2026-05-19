# Yellow.ai API Configuration
## For Automated Prompt Testing

---

## Bot Configuration (per bot)

Each bot has its own values for these three fields. These must be provided at the start of every testing session.

| Field | Description | Example |
|---|---|---|
| `BOT_ID` | Unique bot identifier | `x1750679469050` |
| `API_KEY` | Auth key for this bot | `VhHpCwBlaneIZtbMs9yOQxu7caJPkMMVeZQnkyTp` |
| `BASE_URL` | Regional base URL | `https://r5.nexus.yellow.ai` |

> **Note:** The subdomain (`r5`) may vary per bot region. Confirm the correct base URL from the bot's test link.

---

## Session Identifiers

Each test case must use its own isolated session. Before running a test case, generate:

| Field | How to generate | Example |
|---|---|---|
| `uid` | Random 28-digit numeric string | `7344130842618358844172753266` |
| `sessionId` | Retrieved from the first bot response (see API 1 below) | `6a04635ed99a7c0001277c3a` |
| `messageId` | Current Unix timestamp in milliseconds | `1778671627034` |

To generate a `uid` in Python:
```python
import random
uid = ''.join([str(random.randint(0, 9)) for _ in range(28)])
```

Use the **same `uid`** for all turns in a single test case conversation. Use a **new `uid`** for every new test case.

---

## API 1 — Send Message

Sends a user message to the bot and receives the bot's reply.

### Request

```
POST {BASE_URL}/integrations/yellowmessenger/receive/v3?bottype=production&bot={BOT_ID}
```

**Headers:**
```
Content-Type: application/json
x-api-key: {API_KEY}
x-bot-env: production
accept: */*
origin: https://nexus.yellow.ai
```

**Body:**
```json
{
  "type": "threads.message",
  "from": "{uid}",
  "to": "{BOT_ID}",
  "source": "yellowmessenger",
  "widgetVersion": "v3",
  "stream": true,
  "data": {
    "message": "{user_message_text}",
    "messageId": {current_timestamp_ms},
    "pageUrl": "https://nexus.yellow.ai",
    "isSensitiveInfo": false,
    "subSource": null
  }
}
```

### Streaming response

The API responds with `stream: true` — responses come back as Server-Sent Events (SSE). Read the stream until it closes. The bot's reply is contained in the streamed events.

**How to handle the stream in Python:**
```python
import requests
import json

def send_message(bot_id, api_key, base_url, uid, message, message_id):
    url = f"{base_url}/integrations/yellowmessenger/receive/v3?bottype=production&bot={bot_id}"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "x-bot-env": "production",
        "accept": "*/*",
        "origin": "https://nexus.yellow.ai"
    }
    body = {
        "type": "threads.message",
        "from": uid,
        "to": bot_id,
        "source": "yellowmessenger",
        "widgetVersion": "v3",
        "stream": True,
        "data": {
            "message": message,
            "messageId": message_id,
            "pageUrl": "https://nexus.yellow.ai",
            "isSensitiveInfo": False,
            "subSource": None
        }
    }
    response = requests.post(url, headers=headers, json=body, stream=True)
    
    bot_messages = []
    session_id = None
    
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            if decoded.startswith('data:'):
                try:
                    event_data = json.loads(decoded[5:].strip())
                    # Extract bot message text
                    if 'message' in event_data:
                        bot_messages.append(event_data['message'])
                    # Extract sessionId if present
                    if 'sessionId' in event_data and not session_id:
                        session_id = event_data['sessionId']
                except json.JSONDecodeError:
                    pass
    
    return {
        "bot_messages": bot_messages,
        "session_id": session_id
    }
```

> **Important:** Capture the `sessionId` from the first response — you need it for the conversation logs API (API 2). It typically appears in the first event of the stream.

### Timing between turns

Wait for the full stream to complete before sending the next user message. Do not send the next turn until the bot's response is fully received. A 2-second buffer after stream close is recommended to allow memory writes to settle.

---

## API 2 — Conversation Logs

Retrieves the full conversation transcript for a session, including all messages, tool calls, and memory state.

### Request

```
GET {BASE_URL}/api/agents/data/messages?bot={BOT_ID}&uid={uid}&sessionId={sessionId}
```

**Headers:**
```
accept: application/json
x-api-key: {API_KEY}
origin: https://nexus.yellow.ai
```

### When to call

Call this API **after all turns in a test case are complete** — not after each turn. The logs API gives the full structured transcript including tool call logs, which are needed for Phase 5 evaluation.

### Response structure

Returns an **array** of message objects. **Index [0] is the most recent message.**

To read chronologically, reverse the array before processing.

```python
def get_conversation_logs(bot_id, api_key, base_url, uid, session_id):
    url = f"{base_url}/api/agents/data/messages"
    params = {
        "bot": bot_id,
        "uid": uid,
        "sessionId": session_id
    }
    headers = {
        "accept": "application/json",
        "x-api-key": api_key,
        "origin": "https://nexus.yellow.ai"
    }
    response = requests.get(url, headers=headers, params=params)
    messages = response.json()
    
    # Reverse to get chronological order
    return list(reversed(messages))
```

### What to look for in the logs

When evaluating transcripts in Phase 5, check each message object for:

| Field to check | What it tells you |
|---|---|
| `sender` or `role` | Whether it's a user message or bot message |
| `message` / `text` | The message content |
| `type` | Message type (text, quick_reply, carousel, etc.) |
| Tool call fields | Which workflows were invoked and with what args |
| Memory/variable fields | What was written to memory at that turn |
| `goalStatus` | Whether the agent marked the goal as completed |

> **Note:** Share a sample log response as soon as one is available — the exact field names in the response need to be confirmed before Phase 5 evaluation logic is finalized.

---

## Full Test Case Execution — Step by Step

```python
import random
import time

def run_test_case(bot_id, api_key, base_url, conversation_script):
    """
    conversation_script: list of user message strings in order
    Returns: full conversation log (chronological)
    """
    # 1. Generate unique session identifiers
    uid = ''.join([str(random.randint(0, 9)) for _ in range(28)])
    session_id = None
    
    # 2. Send each turn sequentially
    for i, user_message in enumerate(conversation_script):
        message_id = int(time.time() * 1000)
        result = send_message(bot_id, api_key, base_url, uid, user_message, message_id)
        
        # Capture sessionId from first response
        if i == 0 and result.get('session_id'):
            session_id = result['session_id']
        
        # Wait for bot to finish before next turn
        time.sleep(2)
    
    # 3. Retrieve full conversation log
    if session_id:
        logs = get_conversation_logs(bot_id, api_key, base_url, uid, session_id)
    else:
        logs = []  # Flag as ERROR if no sessionId was captured
    
    return {
        "uid": uid,
        "session_id": session_id,
        "logs": logs
    }
```

---

## Running Multiple Test Cases

Run test cases **sequentially** (not in parallel) to avoid hitting rate limits. Use a 3-second gap between test cases.

```python
def run_test_suite(bot_id, api_key, base_url, test_cases):
    results = {}
    for tc in test_cases:
        print(f"Running {tc['id']}...")
        result = run_test_case(bot_id, api_key, base_url, tc['conversation_script'])
        results[tc['id']] = result
        time.sleep(3)  # Gap between test cases
    return results
```

---

## Error Handling

| Situation | Action |
|---|---|
| Send message API returns non-200 | Retry up to 3 times with 5s wait. If still failing → mark test as `ERROR` (not `FAIL`) |
| Stream closes with no bot messages | Wait 3s and retry the turn once. If still empty → mark as `ERROR` |
| `sessionId` not captured from stream | Retry the first turn. If still missing → mark as `ERROR` and investigate stream format |
| Logs API returns empty array | Wait 5s and retry. Logs may not be immediately available after conversation ends |
| Logs API returns non-200 | Retry up to 3 times. If failing → use only stream messages for evaluation (reduced fidelity) |

`ERROR` results are not counted as prompt failures. They indicate an API or network issue and the test case should be re-run.

---

## Config Template (fill in per bot)

```
BOT_ID    = "x________________"
API_KEY   = "________________________"
BASE_URL  = "https://r5.nexus.yellow.ai"
```

> Store these values at the top of every testing session. Never hardcode them inside test logic.
