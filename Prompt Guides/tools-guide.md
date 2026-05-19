# Tool Creation Guide
**Yellow.ai Builder Platform — Business User Reference**

## Contents

- [Tool Creation Guide](#tool-creation-guide)
  - [Contents](#contents)
- [1. What is a Tool?](#1-what-is-a-tool)
- [2. Tool Structure](#2-tool-structure)
- [3. Writing a Good Name](#3-writing-a-good-name)
- [4. Writing a Good Description](#4-writing-a-good-description)
- [5. Defining Input Schema (Parameters)](#5-defining-input-schema-parameters)
  - [Basic Types](#basic-types)
  - [String Parameters](#string-parameters)
  - [Number Parameters](#number-parameters)
  - [Boolean Parameters](#boolean-parameters)
  - [Enum Parameters (Fixed Choices)](#enum-parameters-fixed-choices)
  - [Array Parameters](#array-parameters)
  - [Object Parameters](#object-parameters)
  - [Required vs Optional](#required-vs-optional)
- [6. Defining Output Schema](#6-defining-output-schema)
  - [Simple Output](#simple-output)
  - [Structured Output](#structured-output)
  - [Output with Lists](#output-with-lists)
- [7. Full Examples](#7-full-examples)
  - [7A. Simple Lookup Tool](#7a-simple-lookup-tool)
  - [7B. Action Tool with Multiple Inputs](#7b-action-tool-with-multiple-inputs)
  - [7C. Search Tool with Optional Filters](#7c-search-tool-with-optional-filters)
- [8. Best Practices](#8-best-practices)
  - [Naming](#naming)
  - [Descriptions](#descriptions)
  - [Input Schema](#input-schema)
  - [Output Schema](#output-schema)
  - [General](#general)
- [9. Common Mistakes](#9-common-mistakes)
- [10. Template](#10-template)


# 1. What is a Tool?

A tool is a **function the AI agent can call** to fetch data, perform an action, or interact with an external system. Think of it as giving the agent hands — without tools, the agent can only talk. With tools, it can look things up, process requests, and take action.

Each tool does **one thing**. If you find yourself describing a tool that "fetches the account and also processes a refund", split it into two tools.

> **Examples of well-scoped tools**
> - Fetch a customer's account details
> - Search the knowledge base for an article
> - Process a refund for a specific charge
> - Send a password reset email

> **Too broad — split these up**
> - Handle all account operations ✗
> - Do everything related to billing ✗

When an agent uses a tool, three things happen:

1. The agent decides to call the tool and fills in the **inputs** (parameters)
2. The platform executes the tool and returns the **output** (result)
3. The agent reads the result and continues the conversation


# 2. Tool Structure

Every tool has four parts:

```
NAME            A unique slug that identifies the tool
DESCRIPTION     What the tool does and when to use it
INPUT SCHEMA    What the tool needs to run (parameters)
OUTPUT SCHEMA   What the tool returns (result shape)
```

Here's how those map to the underlying format:

```json
{
  "type": "function",
  "function": {
    "name": "fetch-account-details",
    "description": "Fetch the customer's account details by email address.",
    "parameters": {
      "type": "object",
      "properties": {
        "email": {
          "type": "string",
          "description": "The customer's registered email address."
        }
      },
      "required": ["email"],
      "additionalProperties": false
    }
  }
}
```


# 3. Writing a Good Name

The name is how the agent refers to the tool in conversation. It should be a short, kebab-case slug that reads like a verb-noun pair.

```
✓  fetch-account-details
✓  process-refund
✓  send-password-reset
✓  search-orders
✓  check-subscription-status
✓  query-knowledgebase

✗  account                     (too vague — fetch? update? delete?)
✗  getAccountDetailsForUser    (use kebab-case, not camelCase)
✗  do-stuff                    (meaningless)
✗  fetch_account_details       (use hyphens, not underscores)
```

**Rules:**
- Use **kebab-case** (words separated by hyphens)
- Start with a **verb**: `fetch`, `search`, `process`, `send`, `check`, `create`, `update`, `cancel`
- Keep it under **4 words**
- Make it match what the tool does — the agent reads the name to decide whether to use it


# 4. Writing a Good Description

The description is the most important part of a tool definition. The AI uses it to decide **when** to call the tool and **what** it does. A vague description leads to the tool being called at the wrong time or not at all.

**Write it like you're explaining to a new colleague what button this is and when to press it.**

```
✓  "Fetch the customer's account details including name,
    email, subscription status, and billing info. Use this
    after the customer has provided their email address."

✗  "Gets account details."
    (too vague — what details? when should it be used?)

✗  "This tool is used for the purpose of retrieving
    customer account information from the database system."
    (too wordy — the AI doesn't need filler)
```

**Rules:**
- **Say what it returns**, not just what it does — "Fetches account details **including name, email, and subscription status**"
- **Say when to use it** — "Use this **after the customer provides their email**"
- **Say when NOT to use it** if there's a common confusion — "Do **not** use this for billing history — use `fetch-billing-history` instead"
- Keep it to **1–3 sentences**
- Don't repeat the name — the agent already has it


# 5. Defining Input Schema (Parameters)

The input schema tells the agent **what information it needs to provide** when calling the tool. Each parameter has a type, a description, and whether it's required or optional.

## Basic Types

| Type | Use for | Example |
|---|---|---|
| `string` | Text values — names, emails, IDs | `"john@example.com"` |
| `number` | Numeric values — amounts, counts, ages | `49.99` |
| `boolean` | Yes/no flags | `true` |
| `array` | Lists of values | `["tag1", "tag2"]` |
| `object` | Nested structured data | `{"street": "...", "city": "..."}` |

## String Parameters

The most common type. Use for any text input.

```json
{
  "email": {
    "type": "string",
    "description": "The customer's registered email address."
  }
}
```

**With a format hint** (helps the agent provide the right shape):

```json
{
  "date_of_birth": {
    "type": "string",
    "description": "The customer's date of birth in YYYY-MM-DD format."
  }
}
```

**With a pattern or constraint described naturally:**

```json
{
  "order_id": {
    "type": "string",
    "description": "The order ID. Always starts with 'ORD-' followed by 8 digits, e.g. ORD-12345678."
  }
}
```

## Number Parameters

Use for amounts, quantities, or any numeric value.

```json
{
  "refund_amount": {
    "type": "number",
    "description": "The refund amount in the customer's local currency. Must be positive."
  }
}
```

## Boolean Parameters

Use for yes/no, true/false flags.

```json
{
  "include_cancelled": {
    "type": "boolean",
    "description": "Whether to include cancelled orders in the results. Defaults to false."
  }
}
```

## Enum Parameters (Fixed Choices)

When a parameter can only be one of a fixed set of values, use `enum`. This prevents the agent from inventing values.

```json
{
  "priority": {
    "type": "string",
    "enum": ["low", "medium", "high", "critical"],
    "description": "The priority level for the support ticket."
  }
}
```

```json
{
  "refund_reason": {
    "type": "string",
    "enum": ["duplicate_charge", "service_issue", "cancellation", "other"],
    "description": "The reason for the refund. Must be one of the allowed values."
  }
}
```

**When to use enum vs free text:**
- Use `enum` when there are **fewer than 10** well-defined options
- Use free text (`string`) when the value is **open-ended** or has too many possibilities

## Array Parameters

Use when the tool accepts a list of values.

```json
{
  "tags": {
    "type": "array",
    "items": {
      "type": "string"
    },
    "description": "Tags to apply to the ticket. Each tag is a string."
  }
}
```

**Array of objects** (for structured lists):

```json
{
  "line_items": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "product_id": {
          "type": "string",
          "description": "The product ID."
        },
        "quantity": {
          "type": "number",
          "description": "Number of units."
        }
      },
      "required": ["product_id", "quantity"]
    },
    "description": "The list of items to include in the order."
  }
}
```

## Object Parameters

Use when you need to group related fields together.

```json
{
  "shipping_address": {
    "type": "object",
    "properties": {
      "street": {
        "type": "string",
        "description": "Street address."
      },
      "city": {
        "type": "string",
        "description": "City name."
      },
      "postal_code": {
        "type": "string",
        "description": "Postal or ZIP code."
      },
      "country": {
        "type": "string",
        "description": "Two-letter country code (e.g. 'US', 'GB')."
      }
    },
    "required": ["street", "city", "country"],
    "additionalProperties": false
  }
}
```

## Required vs Optional

Mark a parameter as **required** when the tool cannot function without it. Mark it as **optional** when there's a sensible default or the parameter is a filter that narrows results.

```json
{
  "type": "object",
  "properties": {
    "email": {
      "type": "string",
      "description": "The customer's email address."
    },
    "include_history": {
      "type": "boolean",
      "description": "Whether to include past interactions. Defaults to false."
    }
  },
  "required": ["email"],
  "additionalProperties": false
}
```

**Rules of thumb:**
- If the tool **will fail** without the parameter → `required`
- If the tool **works fine** without it (uses a default) → optional
- If the agent **always has** this value from context → `required` is fine
- If the agent **might not have** this value → make it optional or don't include it
- **Fewer required parameters = easier for the agent to call the tool correctly**


# 6. Defining Output Schema

The output schema describes what the tool returns. While the AI can work with unstructured text responses, defining a clear output structure helps the agent understand and use the results correctly.

## Simple Output

For tools that return a single piece of information or a confirmation:

```json
{
  "type": "object",
  "properties": {
    "success": {
      "type": "boolean",
      "description": "Whether the operation succeeded."
    },
    "message": {
      "type": "string",
      "description": "A human-readable status message."
    }
  }
}
```

## Structured Output

For tools that return rich data the agent needs to interpret:

```json
{
  "type": "object",
  "properties": {
    "account_id": {
      "type": "string",
      "description": "The unique account identifier."
    },
    "name": {
      "type": "string",
      "description": "The customer's full name."
    },
    "email": {
      "type": "string",
      "description": "The registered email address."
    },
    "subscription_status": {
      "type": "string",
      "enum": ["active", "cancelled", "expired", "suspended"],
      "description": "Current subscription status."
    },
    "is_blocked": {
      "type": "boolean",
      "description": "Whether the account is currently blocked."
    },
    "created_at": {
      "type": "string",
      "description": "Account creation date in ISO 8601 format."
    }
  }
}
```

## Output with Lists

For tools that return search results or collections:

```json
{
  "type": "object",
  "properties": {
    "total_count": {
      "type": "number",
      "description": "Total number of matching orders."
    },
    "orders": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "order_id": {
            "type": "string",
            "description": "The order ID."
          },
          "date": {
            "type": "string",
            "description": "Order date in YYYY-MM-DD format."
          },
          "amount": {
            "type": "number",
            "description": "Order total."
          },
          "status": {
            "type": "string",
            "enum": ["pending", "shipped", "delivered", "returned"],
            "description": "Current order status."
          }
        }
      },
      "description": "List of matching orders."
    }
  }
}
```


# 7. Full Examples

## 7A. Simple Lookup Tool

A tool that takes one input and returns account data.

```json
{
  "type": "function",
  "function": {
    "name": "fetch-account-details",
    "description": "Fetch the customer's account details by email address. Returns name, email, subscription status, and whether the account is blocked. Use this after the customer provides or confirms their email.",
    "parameters": {
      "type": "object",
      "properties": {
        "email": {
          "type": "string",
          "description": "The customer's registered email address."
        }
      },
      "required": ["email"],
      "additionalProperties": false
    }
  }
}
```

**Expected output:**

```json
{
  "account_id": "ACC-12345",
  "name": "Jane Smith",
  "email": "jane@example.com",
  "subscription_status": "active",
  "is_blocked": false,
  "plan": "Premium",
  "created_at": "2024-03-15"
}
```


## 7B. Action Tool with Multiple Inputs

A tool that performs an action and returns a confirmation.

```json
{
  "type": "function",
  "function": {
    "name": "process-refund",
    "description": "Process a refund for a specific charge on the customer's account. Only use this after confirming the charge details with the customer and verifying refund eligibility. Returns a confirmation with the refund reference number.",
    "parameters": {
      "type": "object",
      "properties": {
        "account_id": {
          "type": "string",
          "description": "The customer's account ID (from fetch-account-details)."
        },
        "charge_id": {
          "type": "string",
          "description": "The ID of the specific charge to refund (from fetch-billing-history)."
        },
        "amount": {
          "type": "number",
          "description": "The refund amount. Must match or be less than the original charge amount."
        },
        "reason": {
          "type": "string",
          "enum": ["duplicate_charge", "service_issue", "cancellation", "billing_error", "other"],
          "description": "The reason for the refund."
        }
      },
      "required": ["account_id", "charge_id", "amount", "reason"],
      "additionalProperties": false
    }
  }
}
```

**Expected output:**

```json
{
  "success": true,
  "refund_id": "REF-98765",
  "amount": 49.99,
  "estimated_days": 5,
  "message": "Refund of $49.99 processed. Expected in 3-5 business days."
}
```


## 7C. Search Tool with Optional Filters

A tool that searches with required and optional parameters.

```json
{
  "type": "function",
  "function": {
    "name": "search-orders",
    "description": "Search for orders on a customer's account. Returns a list of matching orders with status, date, and amount. Use after fetching account details. Supports optional filters by date range and status.",
    "parameters": {
      "type": "object",
      "properties": {
        "account_id": {
          "type": "string",
          "description": "The customer's account ID."
        },
        "status": {
          "type": "string",
          "enum": ["pending", "shipped", "delivered", "returned", "cancelled"],
          "description": "Filter by order status. Omit to return all statuses."
        },
        "from_date": {
          "type": "string",
          "description": "Start of date range in YYYY-MM-DD format. Omit for no start bound."
        },
        "to_date": {
          "type": "string",
          "description": "End of date range in YYYY-MM-DD format. Omit for no end bound."
        },
        "limit": {
          "type": "number",
          "description": "Maximum number of orders to return. Defaults to 10."
        }
      },
      "required": ["account_id"],
      "additionalProperties": false
    }
  }
}
```


# 8. Best Practices

## Naming

- **One verb, one noun.** `fetch-account` not `get-and-validate-account-info`
- **Use consistent verbs across tools.** Pick a convention and stick to it:
  - `fetch-*` for reading data
  - `search-*` for queries with filters
  - `create-*` for creating new records
  - `update-*` for modifying existing records
  - `send-*` for triggering notifications or emails
  - `process-*` for multi-step operations (refunds, cancellations)
  - `check-*` for status checks and validations
- **Match the name used in agent prompts.** If the agent references `[fetch-account-details]`, the tool name must be exactly `fetch-account-details`

## Descriptions

- **Describe what it returns**, not just what it does
- **Include when to use it** — "Use this after the customer provides their email"
- **Include when NOT to use it** if there's a sibling tool that's easily confused — "Do not use for billing history, use `fetch-billing-history` instead"
- **Don't describe the parameters in the description** — that's what the parameter descriptions are for

## Input Schema

- **Always set `additionalProperties: false`** — this prevents the agent from passing unexpected fields
- **Write parameter descriptions as if the agent is a new team member** — be specific about format, constraints, and where the value comes from
- **Use `enum` for constrained choices** — the agent will pick from the list instead of inventing values
- **Describe formats in the description**, not just the type — "Date in YYYY-MM-DD format", "Two-letter country code"
- **Tell the agent where values come from** — "The account ID returned by `fetch-account-details`"
- **Keep the number of required parameters low** — fewer inputs means fewer chances for the agent to get it wrong. 1-3 required parameters is ideal
- **Don't ask for data the agent already has from context** — if the customer's email is already in the conversation, the agent can fill it in

## Output Schema

- **Always include a `success` or `status` field for action tools** — the agent needs to know if the operation worked
- **Include a `message` field with a human-readable summary** — the agent can relay this directly to the customer
- **Use consistent field names across tools** — if one tool returns `account_id`, all tools should use `account_id`, not `accountId` or `id`
- **Return only what the agent needs** — don't dump raw database records. Curate the fields
- **Use enums in output too** — `"status": "active"` is more useful than `"status": "Account is currently active and in good standing"`

## General

- **One tool, one job.** If you need to fetch account details AND billing history, make two tools. The agent can call them both
- **Don't duplicate data across tools.** If `fetch-account-details` returns the email, `fetch-billing-history` doesn't need to return it too
- **Test with the agent.** After defining a tool, test whether the agent calls it at the right time with the right parameters. Adjust the description if it doesn't
- **Version carefully.** If you change a tool's parameters, agents that reference the old parameters will break. Add new optional parameters instead of changing existing ones


# 9. Common Mistakes

| Mistake | Why it's a problem | Fix |
|---|---|---|
| Vague description | Agent doesn't know when to call the tool | Describe what it does, returns, and when to use it |
| Too many required parameters | Agent often can't fill them all in | Make non-essential parameters optional with defaults |
| No `additionalProperties: false` | Agent may send unexpected fields that cause errors | Always set it to `false` |
| Missing parameter descriptions | Agent guesses what to pass — and gets it wrong | Describe every parameter: type, format, where it comes from |
| Free text where enum would work | Agent invents values like "High Priority!!" instead of "high" | Use `enum` for constrained values |
| Tool does too many things | Hard to describe, hard for the agent to use correctly | One tool, one job |
| Tool name doesn't match agent reference | Agent prompt says `[fetch-account]` but tool is named `get-account-details` | Keep names in sync |
| No error output defined | Agent doesn't know the tool failed and continues with stale data | Return clear error status and message |
| Inconsistent field names | `account_id` in one tool, `accountId` in another — agent gets confused | Standardize naming across all tools |
| Exposing internal fields | Raw DB columns like `_id`, `__v`, `createdAt_epoch` confuse the agent | Return clean, human-readable field names |


# 10. Template

Copy this when defining a new tool.

```json
{
  "type": "function",
  "function": {
    "name": "[verb]-[noun]",
    "description": "[What this tool does. What it returns. When to use it. When NOT to use it.]",
    "parameters": {
      "type": "object",
      "properties": {
        "[param_name]": {
          "type": "[string | number | boolean | array | object]",
          "description": "[What this parameter is. Expected format. Where the value comes from.]"
        }
      },
      "required": ["[param_name]"],
      "additionalProperties": false
    }
  }
}
```

**Expected output shape** (document alongside the tool):

```json
{
  "success": true,
  "[field_name]": "[description of what this field contains]",
  "message": "[Human-readable summary for the agent to relay]"
}
```

---
*Yellow.ai Builder Platform — Internal Guide v0.1*
