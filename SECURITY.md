# DUREM AI Security Guide

DUREM is designed for internal company knowledge. Security depends on both application controls and the environment where it is installed.

## Threat model

DUREM assumes:

- employees may be curious or accidentally access the wrong policy
- uploaded documents may contain malformed/hostile content
- an attacker may attempt password guessing
- a local LLM may hallucinate or follow prompt injection in retrieved text
- a stolen browser or app API session may be reused
- a backup may be tampered with
- a server may accidentally be exposed beyond the intended LAN

## Authentication

- Argon2id password hashing
- strong password policy
- legacy PBKDF2/scrypt hashes verify only for migration and are rehashed after successful login
- server-side sessions
- signed HttpOnly cookies
- SameSite=Strict
- absolute session expiry
- idle session expiry
- maximum sessions per user
- password reset/change invalidates the user's browser sessions and app API tokens
- admin can revoke browser sessions and app API tokens
- last active admin cannot be accidentally disabled/demoted

### App API authentication

The versioned `/api/v1/...` app API uses Bearer tokens rather than browser cookies. Tokens are opaque random values returned to the client at login. DUREM stores only a SHA-256 token hash together with user, device label, expiry, last-used time, and revocation state.

- raw Bearer tokens are never written to SQLite or audit metadata
- API access can be disabled globally by an admin; doing so revokes active API tokens
- token lifetime is configurable (1–365 days)
- expired/revoked tokens are rejected
- browser CSRF tokens and app Bearer tokens are not interchangeable
- separate webview origins require an explicit `DUREM_CORS_ORIGINS` allow-list; wildcard origins are ignored

## CSRF and browser security

Every state-changing authenticated API requires `X-CSRF-Token`.

Responses include controls such as:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- CSP with self-only script/connect sources
- `frame-ancestors 'none'`
- restrictive Permissions Policy
- HSTS when HTTPS/secure-cookie mode is enabled
- no-store cache policy on authenticated pages/APIs

## Brute-force and abuse controls

- username+IP login limit
- IP-wide login limit to prevent username rotation
- per-user assistant requests/minute
- failed login audit events

The built-in limiter is process-local. For large multi-worker deployments, place an enterprise reverse proxy/rate limiter in front or move throttling state to shared storage.

## Knowledge access

Document retrieval and direct file/preview access enforce:

- `all`, `department`, or `admin` visibility
- department scope
- active/archive lifecycle
- effective-from/effective-to dates

Non-admin employees cannot fetch an archived, future, expired, admin-only, or other-department document even if they know its document ID.

## Upload security

Admin uploads are bounded and validated:

- extension whitelist
- file size limit
- PDF signature checks
- PDF active JavaScript/OpenAction/Launch pattern rejection
- Office container structure checks
- Office file-count and total-uncompressed-size limits
- VBA macro rejection
- OLE/ActiveX/embedded object rejection
- binary-content rejection for plain text formats
- sanitized stored filenames

This is hardening, not a full malware sandbox. In a high-security environment, put an antivirus/CDR scanner before the DUREM upload endpoint.

## LLM safety

DUREM separates decision logic from explanation where practical.

- deterministic threshold rules are resolved in Python
- LLM output must pass a Pydantic schema
- only exact retrieved source IDs are accepted
- unsupported source IDs are dropped
- policy answers without a verified source become `NOT_FOUND`
- malformed model output gets one bounded repair attempt, then fails safe
- retrieved text is explicitly marked untrusted data in the system prompt
- chain-of-thought is not exposed in the employee UI

## Conversational chat and personal memory

General chat is intentionally separated from company-policy authority. The hybrid router runs before retrieval. Clear prompts are routed deterministically; only ambiguous prompts use a classification-only local Qwen call. Policy-sensitive questions are sent to the validated policy path even if the user selected general chat. If the classifier is unavailable or low-confidence while company-policy signals exist, routing fails safe to the policy path.

Personal memory is per-user and non-authoritative. DUREM blocks common credentials/secrets and company-policy/approval claims from being stored as personal memory. General-chat audit logging is metadata-only by default; raw chat prompts require an explicit admin opt-in. Conversation history remains stored as a user-facing feature and should be covered by the organization's retention/privacy policy.

## Local AI data boundary

By default DUREM accepts only loopback/private/link-local Lemonade IPs plus local Docker host bridging.

Public/external model endpoints are rejected unless an operator explicitly sets:

```text
DUREM_ALLOW_EXTERNAL_AI=true
```

For a private product deployment, leave this **false**.

DUREM's `httpx` clients use `trust_env=False` so system HTTP proxy environment variables do not silently redirect local AI requests.

## LAN fail-closed checks

When DUREM binds outside loopback, startup refuses to continue if:

- `DUREM_SECRET_KEY` is weak/default
- `DUREM_ALLOWED_HOSTS=*`

The Windows/Linux setup scripts generate a strong secret and trusted-host list.

## Backup security

Encrypted backup uses AES-256-GCM with a passphrase-derived key. Restore validates:

- encrypted tag/passphrase
- archive size and file count
- traversal/absolute/drive-like paths
- ZIP symlinks
- manifest identity
- database checksum
- SQLite integrity
- required schema

Restore takes a rollback snapshot first and invalidates all restored browser sessions and app API tokens.

## Production checklist

- [ ] TLS/HTTPS configured
- [ ] `DUREM_SECURE_COOKIES=true`
- [ ] explicit `DUREM_ALLOWED_HOSTS`
- [ ] explicit `DUREM_CORS_ORIGINS` only if a separate web/webview origin needs it
- [ ] strong unique `DUREM_SECRET_KEY`
- [ ] Lemonade only on loopback/private network
- [ ] firewall limits employee/admin access to intended LAN/VPN
- [ ] BitLocker/LUKS/full-disk encryption
- [ ] restricted OS account/service permissions
- [ ] current OS/Python/Lemonade versions
- [ ] tested encrypted backup
- [ ] tested restore on a staging copy
- [ ] admin accounts minimized
- [ ] document/rule owners defined
- [ ] malware scanning/CDR added if threat model requires it
- [ ] audit review process assigned

## Reporting a security issue

Treat a suspected DUREM vulnerability as an internal security incident. Preserve logs, isolate the affected instance if needed, rotate credentials/secrets, and review audit/session records before returning it to service.
