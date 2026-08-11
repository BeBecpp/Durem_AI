# Changelog

## 2.2.0-rc1

- Universal hybrid prompt router: deterministic clear-case routing + ambiguous-only local Qwen classifier + conservative fallback.
- Company-policy safety override remains server-side even when Chat mode is requested.
- Route metadata added to assistant responses: method, confidence, classifier invocation, reason, override.
- Versioned App API `/api/v1/...` added.
- Bearer app authentication with hashed token storage, expiry, device sessions, revoke, and password-change invalidation.
- App API endpoints added for route/ask, conversations, memory, feedback, source preview/download, config and health.
- Optional explicit CORS origin allow-list for separate webview/dev clients.
- Admin settings added for hybrid router, App API enable/disable, and token lifetime.
- Admin Security Center now displays/revokes app API sessions.
- API client examples and `API.md` added.
- Automated validation expanded to 32 tests.
