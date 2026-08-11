# DUREM AI 2.2 Architecture

## Product boundary

One DUREM installation serves one organization. Company knowledge, conversations, user memory, rules, and local inference remain inside that installation unless the operator explicitly changes the deployment boundary.

```text
Employee Browser                 Admin Browser
       │                              │
       └──────────── HTTPS/LAN ───────┘
                       │
                 FastAPI / DUREM
                       │
                Hybrid Router
           deterministic → ambiguous Qwen
               ┌───────┴────────┐
               │                │
          POLICY PATH       GENERAL CHAT
               │                │
        Rule Engine          Session history
               │                │
        RAG + ACL          Personal memory
               │                │
        Local Qwen          Local Qwen
               │                │
       Source validation     Natural response
               └───────┬────────┘
                       │
               SQLite + local files
```

## Routing contract

The router runs before retrieval.

- Explicit `policy`, `can_i`, `how_to`, and `who` modes always use the policy path.
- `chat` uses general chat unless policy-sensitive signals trigger a server-side safety override.
- `auto` first evaluates generic company anchors, authority language, policy domains, and general-helper intent.
- High-confidence prompts route deterministically with no extra inference.
- Ambiguous prompts only are classified by local Qwen using a classification-only JSON contract.
- If that classifier is unavailable or low-confidence and a meaningful policy signal exists, DUREM conservatively chooses the policy path.
- A short follow-up after a policy answer stays on the policy path and rebuilds a contextual retrieval query from recent **user** messages.

This is a universal policy-vs-chat router, not a collection of hardcoded discount/leave/vehicle checkers.

## Policy path

```text
Current question + limited previous USER context
        ↓
Scoped rule/document/responsibility retrieval
        ↓
Exact numeric rule available?
  ├─ yes → deterministic Python decision
  └─ no  → Qwen structured JSON
                ↓
        Pydantic schema validation
                ↓
        exact source-ID allow-list
                ↓
 ALLOWED / DENIED / APPROVAL_REQUIRED / NOT_FOUND
```

Policy invariants:

- document ACL and lifecycle filtering happen before model context
- retrieved document text is untrusted data, not instruction
- previous assistant answers and personal memory are never policy authority
- non-`NOT_FOUND` policy answers require accepted source IDs
- approval-required answers require an identified approver

## General chat path

General chat does not call document retrieval.

```text
System boundary + user-owned memory
        +
contiguous recent CHAT messages
        +
current user message
        ↓
Qwen3-8B via Lemonade
        ↓
natural CHAT response
```

The chat system contract permits natural conversation, brainstorming, explanation, rewriting, translation, summarization, coding help, and everyday knowledge work. It prohibits presenting company-specific policy/approval/procedure as fact.

Policy and chat history are separated: when a policy answer is encountered, older policy content is not fed into the normal chat context as authority.

## Personal memory

`user_memories` is a per-user, local SQLite table.

Allowed examples:

- preferred name
- preferred response length
- response language
- tone preference
- explicitly remembered non-sensitive personal context

Blocked from memory:

- passwords, API keys, tokens, OTP/PIN, private keys and similar credentials
- company policy, approval authority, internal limits, or rule claims

Memory is non-authoritative context. Users can inspect or clear it through conversational memory commands. Admins can disable personal memory globally.

## Privacy / audit

Policy answers keep the raw question in audit because `NOT_FOUND` questions feed Knowledge Gaps.

General chat defaults to metadata-only audit:

```json
{
  "route": "chat",
  "answer_type": "CHAT",
  "input_chars": 95,
  "output_chars": 486,
  "latency_ms": 1820,
  "memory_used": true
}
```

Raw general-chat question logging is an explicit admin setting and is off by default. Conversation history itself remains stored per user because it is a user-facing product feature.

## Admin controls

Admin → Settings exposes:

- General chat enabled
- Automatic routing enabled
- Hybrid router enabled
- Personal memory enabled
- Chat history window (4–40 messages)
- Raw general-chat audit logging
- App API access + token lifetime
- existing LLM/embedding/company settings

## Data model

Core SQLite tables:

- `departments`
- `roles`
- `users`
- `sessions`
- `api_tokens` (hashed Bearer tokens for app clients)
- `documents`
- `document_chunks`
- `rules`
- `responsibilities`
- `conversations`
- `messages`
- `user_memories`
- `message_feedback`
- `audit_logs`
- `settings`

Foreign keys are enabled and SQLite WAL mode is used.

## Trust boundaries

- Browser is untrusted.
- Uploaded documents are untrusted content.
- Local model output is untrusted until policy validation succeeds.
- Personal memory is user context, never organization authority.
- Rule-engine records and approved company sources are the policy authority inputs.
- External/public LLM endpoints remain blocked by default.

## Files

```text
app/
├── main.py               FastAPI routes/security middleware/admin controls
├── auth.py               password/browser session/CSRF + app Bearer tokens
├── assistant_router.py   deterministic + ambiguous-only local classifier routing
├── assistant_engine.py   dual-path orchestration
├── memory.py             safe per-user personal memory
├── retrieval.py          scoped policy retrieval
├── documents.py          parsing/ingestion/upload validation
├── lemonade.py           local multi-turn model client
├── backup.py             encrypted backup/restore
├── db.py                 schema/audit/settings
├── models.py             Pydantic contracts
├── rate_limit.py         abuse controls
└── static/
    ├── index.html        Employee assistant UI
    ├── admin.html        Admin UI
    ├── login.html
    ├── app.js
    ├── admin.js
    ├── login.js
    ├── styles.css
    └── mascot.svg
```

## App API boundary

The browser and app authentication surfaces are deliberately separate:

```text
Browser UI → signed HttpOnly cookie + CSRF → legacy /api/...
App client → Bearer token                → versioned /api/v1/...
```

Bearer tokens are random opaque values returned once at login. SQLite stores only a SHA-256 token hash, user, device label, expiry, last-used timestamp, and revocation state. Password resets, user deactivation, restore, or disabling App API access invalidate API sessions.

The versioned assistant API returns routing metadata (`route`, `route_method`, `route_confidence`, `safety_override`) so a future mobile/desktop client can render policy and general-chat responses differently without duplicating routing logic client-side. Clients should still call `/assistant/ask` directly; `/assistant/route` is optional diagnostics/preflight.
