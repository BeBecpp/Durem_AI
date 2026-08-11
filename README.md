# DUREM AI 2.2

**Компанийн дүрэм мэддэг. Бас ярилцаж чаддаг local AI assistant.**

DUREM AI нь хоёр тусдаа execution path-тай: company-policy асуултыг deterministic Rule Engine + RAG + exact-source validation-аар баталгаажуулж хариулна; ердийн chat, brainstorm, тайлбар, бичих тусламж, coding зэрэгт document retrieval шаардахгүйгээр local Qwen model-той natural conversation хийнэ.

Employee тал нь ChatGPT-style chat интерфэйстэй, **“Дүрмээ”** нэртэй хуульч нохой mascot-тай. Admin тал нь knowledge base, decision rules, users/roles/departments, routing, knowledge gaps, audit, security, backup/restore-ийг нэг дор удирдана.

DUREM нь ERP шаарддаггүй. OpenAI/Claude/Gemini API ашигладаггүй. Үндсэн deployment-д application data, document, conversation, audit, model inference бүгд байгууллагын орчинд үлдэнэ.

## DUREM юу хийдэг вэ?

Ажилтан:

> Би харилцагчид 8% хөнгөлөлт шууд өгч болох уу?

DUREM:

```text
ЗӨВШӨӨРӨЛ ШААРДЛАГАТАЙ

5%-иас дээш, 10% хүртэлх хөнгөлөлтөд
борлуулалтын менежерийн зөвшөөрөл шаардлагатай.

Хандах: Борлуулалтын менежер
Эх сурвалж: discount-002
```

Хариултын төлөв:

- `ALLOWED`
- `DENIED`
- `APPROVAL_REQUIRED`
- `NOT_FOUND`

Critical numeric rule (%, MNT, number threshold) таарвал **Python rule engine** шийднэ. Natural-language policy/process/routing асуултыг local Qwen model + RAG шийднэ. Баталгаатай эх сурвалжгүй бол `NOT_FOUND` руу fail-safe хийнэ.

### Hybrid universal router

```text
User message
   ↓
Server-side Router
   ├─ deterministic guard (clear cases)
   ├─ local Qwen classifier (ambiguous cases only)
   └─ conservative safety fallback
          ↓
   ├─ Company / policy → Rule Engine → RAG/ACL → Qwen JSON → source validation
   └─ General chat     → recent chat history + personal memory → Qwen natural response
```

Router нь `discount`, `leave`, `car` гэх мэт тусгай checker-үүдийн цуглуулга биш. Company authority/permission/internal-process гэсэн generic signal-уудыг ашиглана. Тодорхой prompt дээр нэмэлт LLM inference хийхгүй; зөвхөн ambiguous prompt local Qwen classifier руу орно. `Auto` mode нь company authority/permission асуултад policy path руу safety-biased routing хийнэ. User `Ердийн чат` сонгосон байсан ч company-policy signal илэрвэл server policy path руу override хийнэ.

Policy follow-up (`Тэгвэл 12% бол?`) дээр өмнөх user question-оос topic-оо сэргээж retrieval-ийг **шинээр** ажиллуулна; өмнөх AI answer-ийг authority/source гэж ашиглахгүй.

### App API v1

DUREM 2.2 нь mobile/desktop app-д зориулсан versioned API-тай. Browser UI cookie + CSRF-ээ хэвээр ашиглана; first-party app client `/api/v1/...` + Bearer token ашиглана. Raw API token database-д хадгалагдахгүй, зөвхөн hash + expiry/device metadata хадгална.

Гол endpoints:

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/change-password`
- `GET /api/v1/auth/sessions`
- `POST /api/v1/assistant/ask`
- `POST /api/v1/assistant/route` (optional route preview)
- `GET /api/v1/conversations`
- `GET /api/v1/memory`
- `GET /api/v1/documents/{id}/preview`
- `POST /api/v1/feedback`
- `GET /api/v1/health`

Дэлгэрэнгүй: [`API.md`](API.md).

### Personal memory

DUREM user бүрийн explicit preference/context-ийг local SQLite-д тусгаарлан хадгалж чадна. Жишээ: preferred name, response style, language, tone.

- memory нь user-owned, per-account
- company policy/approval authority-г memory болгохгүй
- password, token, API key, OTP зэрэг credential хадгалахгүй
- `Намайг юу санаж байна?`, `...-г март`, `memory-г бүгдийг март` командуудтай
- general-chat audit default-аар raw prompt хадгалахгүй

## Гол боломжууд

### Employee workspace

- Clean ChatGPT-style conversation UI
- `Автомат / Компанийн дүрэм / Ердийн чат` mode switch
- Natural multi-turn general chat
- Server-side company-policy safety override
- Per-user personal memory
- “Дүрмээ” lawyer-dog mascot
- Шинэ chat + conversation history
- Decision cards + confidence
- Approver + next steps
- Source cards + document preview/download
- Answer feedback
- Password change
- Responsive mobile layout

### Admin console

- Overview / runtime health
- Knowledge base
- Knowledge Health score + issues
- PDF / DOCX / XLSX / TXT / MD / CSV upload
- Document version / effective date / archive / activate / reindex
- Department/admin/all visibility
- Rule builder
- Deterministic percent/MNT/number thresholds
- Users / roles / departments
- Responsible-person routing
- Knowledge gaps from `NOT_FOUND`
- Audit trail
- Security Center
- Active session revoke
- AI/model settings + hybrid routing / memory / app API / audit privacy controls
- AES-256-GCM encrypted backup + validated restore

### Local AI

Default:

```text
LLM:       Qwen3-8B-GGUF
Embedding: Qwen3-Embedding-0.6B-GGUF
Runtime:   Lemonade Server
```

DUREM talks to Lemonade through its local OpenAI-compatible API.

The application intentionally rejects a public/external AI endpoint by default. `LEMONADE_BASE_URL` must be loopback/private/local unless the operator explicitly sets `DUREM_ALLOW_EXTERNAL_AI=true`.

## Windows quick start

### 1. Lemonade / model

Your current Ryzen + Radeon setup can use Vulkan acceleration.

```powershell
lemonade backends install llamacpp:vulkan
lemonade pull Qwen3-8B-GGUF
lemonade pull Qwen3-Embedding-0.6B-GGUF
```

Optional helper:

```powershell
.\setup-amd-windows.ps1
```

### 2. DUREM setup

Recommended on Windows: double-click `setup.bat`. It runs the setup with an execution-policy bypass only for that child PowerShell process; it does not permanently change the machine policy.

Or from PowerShell:

```powershell
cd DUREM-AI-2.2
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` is Windows PowerShell 5.1 compatible and is saved with a UTF-8 BOM so Mongolian setup text renders correctly on legacy Windows PowerShell.

Setup asks for:

- company name
- local/LAN bind mode
- trusted host values
- strong admin password

It generates a random application secret and removes the bootstrap password from `.env` after the initial admin is created.

### 3. Start

```powershell
.\start.ps1
```

Default local URL:

```text
http://127.0.0.1:8080
```

Diagnostics:

```powershell
.\diagnose.ps1
```

`start.ps1` attempts to load the configured local model before starting DUREM.

## Linux quick start

```bash
chmod +x setup.sh start.sh
./setup.sh
./start.sh
```

For actual LAN use, read [DEPLOYMENT.md](DEPLOYMENT.md) first.

## Docker

Docker mode expects Lemonade on the host machine by default.

```bash
docker compose up -d --build
```

The container runs as a non-root user, drops capabilities, uses a read-only root filesystem, and stores DUREM runtime data in a named volume.

Before LAN deployment, create a real `.env` with a strong random secret and explicit trusted hosts.

## First configuration workflow

1. **Admin → Organization**: departments, roles, employees.
2. **Admin → Knowledge**: approved policies/documents upload.
3. Archive outdated versions; keep only the effective version active.
4. **Admin → Decision Rules**: critical permissions/thresholds as explicit rules.
5. **Admin → Routing**: HR, Legal, Finance, IT etc. responsible people.
6. **Admin → Knowledge gaps**: unanswered questions review.
7. **Admin → Security**: security score/actions review.
8. Test with real employee accounts and real questions.

## Knowledge pipeline

```text
Approved file
   ↓
Upload validation
   ↓
Text extraction
   ↓
Sections + chunking
   ↓
Local embeddings (optional)
   ↓
Hybrid lexical + semantic retrieval
   ↓
Role/department ACL + lifecycle filter
   ↓
Rule Engine OR Local LLM
   ↓
Source validation
   ↓
Employee answer
```

If the embedding runtime is unavailable, document ingestion falls back to lexical indexing instead of destroying the upload.

## Decision safety model

DUREM does not ask the LLM to freely invent company decisions.

```text
Question
  ↓
User role + department
  ↓
Relevant rules/documents/routing
  ↓
Exact numeric rule available?
  ├─ YES → deterministic code decision
  └─ NO  → local Qwen + strict structured schema
                 ↓
          exact-source validation
                 ↓
       insufficient evidence?
          └─ NOT_FOUND
```

Retrieved document text is treated as **untrusted data**. Prompt-injection-like instructions inside uploaded documents are not supposed to override DUREM's system contract.

## Security highlights

- Argon2id password hashing
- automatic legacy PBKDF2/scrypt hash upgrade
- HttpOnly + SameSite=Strict signed session cookie
- server-side sessions with absolute + idle expiry
- max sessions per user
- CSRF token on state-changing APIs
- login throttling per username/IP and per IP
- per-user assistant request limiting
- TrustedHost middleware
- restrictive CSP / frame denial / MIME sniffing protection
- API docs disabled by default
- last-active-admin lockout protection
- document ACL + active/effective lifecycle enforcement
- upload signature/type/size validation
- Office macro/OLE/ActiveX/embedded-object rejection
- PDF active-action checks
- ZIP bomb/path traversal/symlink protections
- local AI boundary enforcement
- HTTP client ignores system proxy environment for AI requests
- audit logs
- encrypted backups + integrity-checked restore
- all sessions invalidated after restore

See [SECURITY.md](SECURITY.md).

## Backup

Admin → Settings → **Encrypted backup**.

Backup includes:

- SQLite database
- company documents
- manifest/checksum metadata

Encrypted `.durem` format:

- AES-256-GCM
- passphrase-derived key
- authenticated header
- database SHA-256 validation

Restore performs archive/path checks, database integrity checks, schema checks, checksum validation, rollback snapshot creation, migration, then forces all users to log in again.

**The backup passphrase is not stored by DUREM.**

## Important production notes

DUREM 2.2 is a production-minded release candidate, not a substitute for infrastructure hardening. Before giving it to an entire organization:

- use HTTPS/TLS
- set `DUREM_SECURE_COOKIES=true`
- configure explicit `DUREM_ALLOWED_HOSTS`
- use BitLocker/LUKS or equivalent disk encryption
- firewall DUREM/Lemonade to trusted LAN segments
- keep OS/Python/Lemonade patched
- keep Lemonade local/private
- test backup **and restore**
- review the actual company documents/rules loaded into the system

See [DEPLOYMENT.md](DEPLOYMENT.md).

## Test status

Release-candidate validation includes:

- Python compile check
- frontend JavaScript syntax check
- HTML ID/reference static check
- automated tests
- API smoke covering authentication, deterministic decision, knowledge ingestion/preview, encrypted backup/restore, and session invalidation

See [BUILD-REPORT.md](BUILD-REPORT.md).

## Admin password reset

If the local admin password is forgotten, do **not** write a plaintext password directly into SQLite. DUREM stores Argon2id password hashes. Stop DUREM, then run:

```powershell
.\reset-admin.ps1
```

On Windows you can also double-click `reset-admin.bat`. The tool asks for the username (defaults to `admin`) and a new password, updates the Argon2id hash, re-enables the account, revokes all existing sessions for that user, and writes a security audit entry.

The SQLite database is stored at `data/durem.db` unless `DUREM_DATA_DIR` is overridden.

### Clean distribution note (rc3)

The distributable ZIP intentionally contains **no prebuilt `data/durem.db` and no `bootstrap_admin.txt`**. `setup.ps1` creates the database and first admin locally on the target machine. This prevents test credentials or test database state from being shipped to customers.
