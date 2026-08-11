# DUREM AI 2.2 — Build Report

Release label: **2.2.0-rc1**

## Delivered

### Universal hybrid routing

- replaced the release routing contract with a generic policy-vs-chat routing layer
- no business feature is implemented as a special `discount checker`, `leave checker`, or `vehicle checker`
- clear policy/company-authority prompts route deterministically
- clear general-help prompts route deterministically
- only ambiguous prompts invoke a classification-only local Qwen call
- classifier output is strict JSON: route + confidence + machine reason code
- low-confidence/unavailable classifier uses conservative policy fallback when meaningful policy signals exist
- manual `chat` mode remains advisory and cannot bypass strong company-policy signals
- short follow-ups after policy answers stay source-backed and rebuild retrieval context from previous user turns

### Policy safety preserved

- existing numeric Rule Engine remains generic (`percent`, `mnt`, `number` + range + scope + decision + approver)
- RAG is executed only on the policy path
- document ACL and effective-date lifecycle filtering remain before LLM context
- retrieved document text remains untrusted data
- source IDs remain allow-listed against retrieved context
- non-`NOT_FOUND` policy responses still require approved sources
- previous assistant messages and personal memory are never company authority

### App API v1

- versioned `/api/v1/...` REST surface added for future mobile/desktop apps
- Bearer-token login independent of browser cookie/CSRF flow
- opaque random API token returned only to the client; database stores SHA-256 hash only
- per-token device name, expiry, last-used metadata and revocation
- password change / user disable / restore revoke app tokens
- disabling App API from Admin revokes active tokens
- optional explicit CORS allow-list for separately hosted/webview clients
- Admin Security Center lists and revokes app Bearer sessions separately

Main API endpoints:

```text
GET    /api/v1/meta
POST   /api/v1/auth/login
GET    /api/v1/auth/me
DELETE /api/v1/auth/session
GET    /api/v1/config
GET    /api/v1/health
POST   /api/v1/assistant/route
POST   /api/v1/assistant/ask
GET    /api/v1/conversations
GET    /api/v1/conversations/{id}
DELETE /api/v1/conversations/{id}
GET    /api/v1/memory
DELETE /api/v1/memory
```

See `API.md` and `examples/`.

### Response contract for future apps

Assistant responses now include:

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

The client never needs to reproduce company routing logic. It can render `route=policy` with source/decision UI and `route=chat` as a normal assistant conversation.

### Personal memory and privacy

- existing per-user personal memory retained
- credential and company-authority memory rejection retained
- general-chat raw prompts remain excluded from audit by default
- route method/confidence metadata is auditable without storing raw general-chat text

## Validation completed

### Automated suite

```text
32 passed
```

New 2.2 tests include:

- obvious general prompt bypasses classifier
- ambiguous prompt invokes local classifier
- low-confidence chat classification with policy signal fails safe to policy
- classifier runtime failure fails safe
- manual Chat mode cannot bypass company policy
- app Bearer login works
- raw Bearer token is not stored in SQLite
- token revocation works
- expired token is rejected
- `/api/v1/assistant/route` returns stable route metadata
- `/api/v1/assistant/ask` preserves separate chat/policy contracts
- another user's conversation cannot be fetched through app API
- app session listing exposes no token hash and marks the current device
- app password change revokes browser + API sessions
- app source preview/download re-enforces document lifecycle

Existing password, parsing, rule-boundary, upload hardening, encrypted backup, security-header, chat-memory, and policy-routing tests remain green.

### Static checks

```text
python -m compileall app    passed
node --check app.js         passed
node --check admin.js       passed
node --check login.js       passed
```

Employee static IDs resolve. Admin form/modal IDs include intentionally runtime-generated modal fields.

## Security notes

- Browser UI continues to use signed HttpOnly cookie + CSRF.
- App API uses Bearer tokens only.
- API tokens are not interchangeable with browser sessions.
- Wildcard CORS origins are ignored; configure an explicit allow-list only when needed.
- Keep DUREM and Lemonade private/local and use HTTPS for LAN deployments.
- No web browsing, external agents, autonomous company actions, or external AI dependency were added.
