# DUREM App API v1

DUREM 2.2 exposes a versioned REST API for future mobile, desktop, kiosk, Electron/Tauri, Flutter, React Native, or other first-party clients.

The existing browser UI continues to use signed cookies + CSRF. App clients use **Bearer tokens** and `/api/v1/...` endpoints.

## Base flow

```text
POST /api/v1/auth/login
        ↓
Bearer token
        ↓
POST /api/v1/assistant/ask
        ↓
Server-side hybrid router
   ├─ policy → Rule Engine / RAG / sources
   └─ chat   → multi-turn local Qwen
```

The raw bearer token is returned only at login. DUREM stores only its SHA-256 hash, device label, expiry, and usage metadata. Native clients should keep the raw token in the platform secure credential store/keychain rather than plain localStorage or a source/config file.

## Authentication

### Login

`POST /api/v1/auth/login`

```json
{
  "username": "employee01",
  "password": "...",
  "device_name": "DUREM Android"
}
```

Response:

```json
{
  "access_token": "durem_v1_...",
  "token_type": "bearer",
  "expires_at": "2026-09-10T02:00:00+00:00",
  "user": {
    "id": 12,
    "username": "employee01",
    "name": "Employee",
    "department": "Sales",
    "role": "Employee",
    "is_admin": false
  },
  "api_version": "v1"
}
```

Subsequent requests:

```http
Authorization: Bearer durem_v1_...
Content-Type: application/json
```

### Current account

`GET /api/v1/auth/me`

### Change password

`POST /api/v1/auth/change-password`

A successful password change revokes browser sessions and all app API tokens.

### Device/app sessions

- `GET /api/v1/auth/sessions`
- `DELETE /api/v1/auth/sessions/{token_id}`

### Revoke current app token

`DELETE /api/v1/auth/session`

Changing the user's password or disabling the user revokes both browser sessions and app API tokens.

## Assistant

### Ask

`POST /api/v1/assistant/ask`

```json
{
  "question": "Манай компанид маргааш 2 цаг эрт гарч болох уу?",
  "mode": "auto",
  "conversation_id": null
}
```

Supported modes:

- `auto` — recommended
- `policy` — force source-backed policy path
- `chat` — request normal chat; server can still safety-override to policy
- `can_i`, `how_to`, `who` — legacy policy-oriented modes retained for compatibility

Important response routing fields:

```json
{
  "route": "policy",
  "requested_mode": "auto",
  "route_reason": "deterministic:policy_signal",
  "route_confidence": 0.99,
  "route_method": "deterministic",
  "classifier_invoked": false,
  "safety_override": false
}
```

A normal chat response uses `answer_type: "CHAT"` and has no company sources. A policy response remains subject to the existing source validation contract. If approved evidence is insufficient, DUREM returns `NOT_FOUND` rather than using general-chat knowledge.

### Route preview

`POST /api/v1/assistant/route`

This endpoint **only classifies** a prompt. It does not retrieve policy documents and does not generate an answer. It is useful for diagnostics and client UI previews, but clients do not need to call it before `/assistant/ask` because `/assistant/ask` always routes server-side.

```json
{
  "question": "NDA дээр юуг анхаарах вэ?",
  "mode": "auto",
  "conversation_id": null
}
```

Response:

```json
{
  "route": "chat",
  "requested_mode": "auto",
  "route_reason": "classifier:general_explanation",
  "route_confidence": 0.91,
  "route_method": "llm_classifier",
  "safety_override": false,
  "classifier_invoked": true,
  "signals": ["policy_domain"]
}
```

## Hybrid router contract

DUREM does not maintain a separate `discount checker`, `leave checker`, or `car checker`.

The router is domain-generic:

```text
prompt
  ↓
1. explicit mode / policy follow-up guard
  ↓
2. deterministic company-authority signals
  ↓
3. obvious general-chat signals
  ↓
4. ambiguous-only local Qwen classifier
  ↓
5. conservative fallback if classifier is unavailable
```

Clear prompts therefore do not pay an extra classifier inference. Ambiguous prompts can use the same local Qwen runtime as a classification-only component. The classifier never answers the question.

Safety asymmetry: when an ambiguous classifier fails and meaningful company-policy signals exist, DUREM chooses the policy path. A false-policy route can safely end in `NOT_FOUND`; a false-chat route could otherwise fabricate company authority.

## Conversations

- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `DELETE /api/v1/conversations/{conversation_id}`

Conversation ownership is checked against the authenticated user. A token cannot read another user's conversation.

## Sources and feedback

Policy source cards can be opened by app clients without falling back to browser-cookie endpoints:

- `GET /api/v1/documents/{document_id}/preview`
- `GET /api/v1/documents/{document_id}/file`
- `POST /api/v1/feedback`

Document visibility, department scope, archive status, and effective dates are rechecked on these endpoints.

## Personal memory

- `GET /api/v1/memory`
- `DELETE /api/v1/memory`

Natural-language memory commands still work through `/assistant/ask`. Memory remains user-owned context and is never policy authority.

## Runtime / config

- `GET /api/v1/meta` — public API-version/capability discovery
- `GET /api/v1/config` — authenticated product config
- `GET /api/v1/health` — authenticated runtime health

## Admin controls

Admin → Settings controls:

- General chat
- Automatic routing
- Hybrid router
- Personal memory
- Chat history window
- Raw general-chat audit logging
- App API access
- App token lifetime

Disabling App API access revokes active API tokens.

Admin → Security shows browser sessions and active app Bearer sessions separately and supports revocation.

## CORS for a separate app origin

Native apps do not normally need browser CORS. For a webview or separately hosted frontend, configure an explicit allow-list:

```dotenv
DUREM_CORS_ORIGINS=http://localhost:5173,tauri://localhost
```

Wildcard origins are intentionally ignored. Keep the API on trusted LAN/VPN or localhost and use HTTPS for network deployment.

## OpenAPI

FastAPI docs stay disabled by default. For local development only:

```dotenv
DUREM_ENABLE_DOCS=true
```

Then `/docs` and `/openapi.json` are available.
