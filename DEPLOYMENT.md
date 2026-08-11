# DUREM AI Deployment Guide

## Deployment profiles

### 1. Local workstation / development

```text
DUREM_HOST=127.0.0.1
DUREM_ALLOWED_HOSTS=127.0.0.1,localhost
DUREM_SECURE_COOKIES=false
LEMONADE_BASE_URL=http://127.0.0.1:13305
```

Use this while building and testing.

### 2. Internal LAN server

Use a fixed private IP/hostname and HTTPS reverse proxy.

Recommended topology:

```text
Employees
   ↓ HTTPS
Caddy/Nginx
   ↓ loopback HTTP
DUREM AI :8080
   ↓ loopback/private
Lemonade :13305
   ↓
CPU / Radeon / NVIDIA / supported NPU runtime
```

Recommended DUREM application binding behind a same-host proxy:

```text
DUREM_HOST=127.0.0.1
DUREM_ALLOWED_HOSTS=ai.company.local,127.0.0.1,localhost
DUREM_SECURE_COOKIES=true
LEMONADE_BASE_URL=http://127.0.0.1:13305
DUREM_ALLOW_EXTERNAL_AI=false
```

If you intentionally bind Uvicorn directly to `0.0.0.0`, DUREM requires a strong secret and explicit trusted hosts. Direct HTTP is not recommended for an organization-wide deployment.

## Caddy example

See [`deploy/Caddyfile.example`](deploy/Caddyfile.example).

For a private network without a public CA, Caddy's internal CA can be used, but client devices must trust that CA. Coordinate this with the organization's IT administrator.

## Windows service strategy

For initial testing, use:

```powershell
.\start.ps1
```

For continuous operation, run DUREM and Lemonade under a managed service mechanism approved by the organization's IT team. Configure automatic restart, a dedicated OS account where practical, and log rotation.

## Firewall

Expose only the HTTPS reverse-proxy port to employee networks.

Prefer:

```text
443 → employee LAN/VPN
8080 → localhost only
13305 → localhost only
```

Do not expose Lemonade directly to the internet.

## TLS

Once TLS is working:

```text
DUREM_SECURE_COOKIES=true
```

Do not enable secure cookies while accessing DUREM through plain `http://`, because browsers will not send Secure cookies over HTTP.

## Storage

DUREM stores runtime state under `DUREM_DATA_DIR`:

```text
data/
├── durem.db
├── documents/
└── backups/
```

Recommendations:

- put the directory on encrypted storage
- restrict filesystem permissions
- do not sync it to consumer/public cloud storage
- monitor free space
- back it up using the encrypted DUREM backup workflow

## Trusted hosts

Never use `DUREM_ALLOWED_HOSTS=*` on a LAN deployment.

Example:

```text
DUREM_ALLOWED_HOSTS=ai.company.local,10.0.10.20,127.0.0.1,localhost
```

## AI endpoint

Preferred:

```text
LEMONADE_BASE_URL=http://127.0.0.1:13305
DUREM_ALLOW_EXTERNAL_AI=false
```

Private RFC1918 IP endpoints are accepted for a separate LAN inference machine. Public endpoints are blocked by default.

## Docker

`compose.yaml` runs DUREM with:

- non-root user
- read-only root filesystem
- capability drop
- no-new-privileges
- tmpfs for temporary data
- named data volume

The default compose maps DUREM `8080` and connects to host Lemonade through `host.docker.internal`.

For a serious LAN installation, put the container behind TLS and restrict host firewall rules.

## Backup/restore operations

Create encrypted backups from Admin → Settings.

Operational rule:

1. create backup
2. copy it to approved protected storage
3. periodically restore a backup into a non-production DUREM instance
4. verify documents/users/rules
5. record restore-test date

A backup that has never been restored is not a proven backup.

## Upgrade process

Before an upgrade:

1. encrypted backup
2. stop DUREM
3. copy application release
4. preserve `.env` and `DUREM_DATA_DIR`
5. run setup/migration
6. start
7. login
8. check Security Center + Knowledge Health
9. test 5-10 known employee questions

## Go-live acceptance

- authentication works
- admin password changed/controlled
- local model responds
- knowledge retrieval returns correct citations
- role/department ACL tested with two different users
- archived/expired document cannot be opened by employee
- critical threshold rules tested on boundaries
- NOT_FOUND works on unsupported questions
- backup and restore tested
- HTTPS/security headers reviewed
- Security Center reviewed

## Mobile / desktop app API

DUREM 2.2 exposes `/api/v1/...` with Bearer authentication for first-party app clients. Keep that API on the same trusted localhost/LAN/VPN boundary as the browser UI.

For a native mobile/desktop app, no CORS setting is normally required. If a separate browser/webview origin needs access, configure only the exact origins required:

```text
DUREM_CORS_ORIGINS=http://localhost:5173,tauri://localhost
```

Do not expose the API to the public internet merely because it has Bearer authentication. Use TLS, firewall/VPN controls, short-enough token lifetimes, and Admin → Security token revocation.
