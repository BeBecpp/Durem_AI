$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Convert-SecureStringToPlain([Security.SecureString]$Secure) {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Test-DuremPassword([string]$Password) {
  if ($Password.Length -lt 12) { return $false }
  $classes = 0
  if ($Password -cmatch '[a-z]') { $classes++ }
  if ($Password -cmatch '[A-Z]') { $classes++ }
  if ($Password -match '[0-9]') { $classes++ }
  if ($Password -match '[^a-zA-Z0-9\s]') { $classes++ }
  return $classes -ge 3
}

Write-Host ""
Write-Host "DUREM AI 2.2 Setup" -ForegroundColor Cyan
Write-Host "==================" -ForegroundColor DarkGray

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python 3.11+ олдсонгүй. Python суулгаад дахин ажиллуулна уу."
}
& python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ шаардлагатай." }

if (-not (Test-Path ".venv")) {
  Write-Host "[1/5] Virtual environment үүсгэж байна..."
  python -m venv .venv
}

Write-Host "[2/5] Dependencies суулгаж байна..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\pip.exe install -r requirements.txt

if (-not (Test-Path ".env")) {
  Write-Host "[3/5] Secure local configuration үүсгэж байна..."
  $company = Read-Host "Company name [Сутайн Буянт]"
  if ([string]::IsNullOrWhiteSpace($company)) { $company = "Сутайн Буянт" }

  $bind = Read-Host "Bind host [127.0.0.1] (LAN-д 0.0.0.0)"
  if ([string]::IsNullOrWhiteSpace($bind)) { $bind = "127.0.0.1" }
  if ($bind -eq "127.0.0.1") {
    $allowed = "127.0.0.1,localhost"
  } else {
    $hostNames = @("localhost", "127.0.0.1", $env:COMPUTERNAME)
    try {
      $localIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -ne "0.0.0.0" } |
        Select-Object -ExpandProperty IPAddress -Unique
      $hostNames += $localIps
    } catch { }
    $extraHost = Read-Host "Нэмэлт LAN hostname/IP (optional)"
    if (-not [string]::IsNullOrWhiteSpace($extraHost)) { $hostNames += $extraHost.Trim() }
    $allowed = ($hostNames | Where-Object { $_ } | Select-Object -Unique) -join ","
    Write-Host "Trusted hosts: $allowed" -ForegroundColor DarkGray
  }

  do {
    $passwordSecure = Read-Host "Admin password (12+ chars, 3 character classes)" -AsSecureString
    $password = Convert-SecureStringToPlain $passwordSecure
    if (-not (Test-DuremPassword $password)) {
      Write-Host "Password policy: 12+ тэмдэгт, жижиг/том үсэг/тоо/тусгай тэмдэгтийн 3 төрлийг ашиглана." -ForegroundColor Yellow
    }
  } until (Test-DuremPassword $password)

  # PowerShell 5.1 / .NET Framework compatible cryptographic random secret.
  # RandomNumberGenerator.Fill() is not available on older Windows PowerShell runtimes.
  $secretBytes = New-Object byte[] 64
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($secretBytes)
  } finally {
    if ($null -ne $rng) { $rng.Dispose() }
  }
  $secret = [Convert]::ToBase64String($secretBytes)

  $envText = @"
DUREM_COMPANY_NAME=$company
DUREM_HOST=$bind
DUREM_PORT=8080
DUREM_SECRET_KEY=$secret
DUREM_BOOTSTRAP_PASSWORD=$password
DUREM_SESSION_HOURS=12
DUREM_SESSION_IDLE_MINUTES=120
DUREM_MAX_SESSIONS_PER_USER=8
DUREM_PASSWORD_MIN_LENGTH=12
DUREM_SECURE_COOKIES=false
DUREM_ENABLE_DOCS=false
DUREM_ALLOWED_HOSTS=$allowed
DUREM_RATE_LIMIT_PER_MINUTE=20
DUREM_LOGIN_ATTEMPTS_PER_10M=10
DUREM_LOGIN_IP_ATTEMPTS_PER_10M=30
DUREM_UPLOAD_MAX_MB=40
DUREM_UPLOAD_MAX_UNCOMPRESSED_MB=250
DUREM_RESTORE_MAX_MB=2048
LEMONADE_BASE_URL=http://127.0.0.1:13305
LEMONADE_API_KEY=
DUREM_ALLOW_EXTERNAL_AI=false
LEMONADE_MODEL=Qwen3-8B-GGUF
LEMONADE_EMBEDDING_MODEL=Qwen3-Embedding-0.6B-GGUF
LEMONADE_TIMEOUT_SECONDS=240
DUREM_LLM_MAX_TOKENS=900
DUREM_DISABLE_MODEL_THINKING=true
DUREM_EMBEDDINGS_ENABLED=true
DUREM_MOCK_MODE=false
DUREM_DEMO_DATA=false
"@
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $envText, (New-Object Text.UTF8Encoding($false)))
} else {
  Write-Host "[3/5] .env байгаа тул хэвээр үлдээлээ."
}

Write-Host "[4/5] Database initialize/migrate хийж байна..."
& .\.venv\Scripts\python.exe -c "from app.db import init_db; from app.auth import ensure_default_admin; init_db(); ensure_default_admin(); print('Database ready')"
# Bootstrap password зөвхөн анхны user үүсгэхэд хэрэгтэй. Disk дээр удаан хадгалахгүй.
if (Test-Path ".env") {
  $envRaw = [IO.File]::ReadAllText((Join-Path $PSScriptRoot ".env"))
  $envRaw = [Text.RegularExpressions.Regex]::Replace($envRaw, '(?m)^DUREM_BOOTSTRAP_PASSWORD=.*$', 'DUREM_BOOTSTRAP_PASSWORD=')
  [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $envRaw, (New-Object Text.UTF8Encoding($false)))
  try { icacls ".env" /inheritance:r /grant:r "${env:USERNAME}:(R,W)" | Out-Null } catch { }
}
$password = $null
$passwordSecure = $null

Write-Host "[5/5] Local AI шалгаж байна..."
if (Get-Command lemonade -ErrorAction SilentlyContinue) {
  Write-Host "Lemonade detected." -ForegroundColor Green
  try { lemonade status } catch { }
  Write-Host "AMD/Radeon setup хэрэгтэй бол: .\setup-amd-windows.ps1" -ForegroundColor DarkGray
} else {
  Write-Host "Lemonade CLI PATH дээр олдсонгүй. Backend суулаа; AI-г тусад нь асаана." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup дууслаа. .\start.ps1 ажиллуулна уу." -ForegroundColor Green
