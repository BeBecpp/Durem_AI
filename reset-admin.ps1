$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Convert-SecureStringToPlain([Security.SecureString]$Secure) {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

Write-Host ""
Write-Host "DUREM AI - Local Password Reset" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor DarkGray
Write-Host "This tool updates the Argon2id password hash and revokes existing sessions." -ForegroundColor DarkGray
Write-Host ""

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw ".venv олдсонгүй. Эхлээд setup.bat / setup.ps1 ажиллуулна уу."
}

$db = Join-Path $PSScriptRoot "data\durem.db"
if (-not (Test-Path $db)) {
  throw "data\durem.db олдсонгүй. Setup бүрэн дууссан эсэхийг шалгана уу."
}

$username = Read-Host "Username [admin]"
if ([string]::IsNullOrWhiteSpace($username)) { $username = "admin" }
$username = $username.Trim()

$passwordSecure = Read-Host "New password (12+ chars, 3 character classes)" -AsSecureString
$password = Convert-SecureStringToPlain $passwordSecure

$env:DUREM_RESET_USERNAME = $username
$env:DUREM_RESET_PASSWORD = $password

try {
  $script = @'
import json
import os
import sys

from app.auth import hash_password, password_policy_error
from app.db import connect, now_iso

username = os.environ.get("DUREM_RESET_USERNAME", "").strip()
password = os.environ.get("DUREM_RESET_PASSWORD", "")

error = password_policy_error(password)
if error:
    print(f"ERROR: {error}")
    raise SystemExit(2)

with connect() as conn:
    row = conn.execute(
        "SELECT id, username, name, active FROM users WHERE username=? COLLATE NOCASE",
        (username,),
    ).fetchone()
    if not row:
        print(f"ERROR: User not found: {username}")
        print("Available users:")
        for item in conn.execute("SELECT username, name, active FROM users ORDER BY username").fetchall():
            print(f"  - {item['username']} | {item['name']} | active={item['active']}")
        raise SystemExit(3)

    stamp = now_iso()
    conn.execute(
        "UPDATE users SET password_hash=?, active=1, updated_at=? WHERE id=?",
        (hash_password(password), stamp, row["id"]),
    )
    conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    conn.execute(
        "INSERT INTO audit_logs(user_id,event_type,action,metadata_json,created_at) VALUES(?,?,?,?,?)",
        (
            row["id"],
            "security",
            "local_password_reset",
            json.dumps({"username": row["username"], "sessions_revoked": True}, ensure_ascii=False),
            stamp,
        ),
    )

print(f"OK: Password reset completed for '{username}'. Existing sessions were revoked.")
'@

  $script | & $python -
  if ($LASTEXITCODE -ne 0) {
    throw "Password reset failed (exit code $LASTEXITCODE)."
  }

  Write-Host ""
  Write-Host "Password reset амжилттай." -ForegroundColor Green
  Write-Host "Username: $username" -ForegroundColor Green
  Write-Host "Одоо DUREM-ээ асаагаад шинэ password-оор нэвтэрнэ үү." -ForegroundColor Green
}
finally {
  Remove-Item Env:DUREM_RESET_USERNAME -ErrorAction SilentlyContinue
  Remove-Item Env:DUREM_RESET_PASSWORD -ErrorAction SilentlyContinue
  $password = $null
  $passwordSecure = $null
}
