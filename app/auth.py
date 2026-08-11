from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import settings
from .db import connect, get_setting, now_iso

SESSION_COOKIE = "durem_session"
CSRF_HEADER = "X-CSRF-Token"
PBKDF2_ROUNDS = 260_000  # legacy verifier
# Argon2id is the default password KDF. Scrypt/PBKDF2 remain verifier-only for seamless upgrades.
ARGON2 = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, salt_len=16)

COMMON_PASSWORDS = {
    "password", "password123", "12345678", "123456789", "qwerty123",
    "admin123", "durem@12345", "changeme", "welcome123", "company123",
}


def _signed_session_cookie(session_id: str) -> str:
    signature = hmac.new(settings.secret_key.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{session_id}.{signature}"


def _parse_session_cookie(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    session_id, signature = value.rsplit(".", 1)
    expected = hmac.new(settings.secret_key.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return session_id


def password_policy_error(password: str) -> str | None:
    if len(password) < settings.password_min_length:
        return f"Нууц үг хамгийн багадаа {settings.password_min_length} тэмдэгт байна."
    classes = 0
    classes += any(c.islower() for c in password)
    classes += any(c.isupper() for c in password)
    classes += any(c.isdigit() for c in password)
    classes += any(c in string.punctuation or (not c.isalnum() and not c.isspace()) for c in password)
    if classes < 3:
        return "Нууц үг жижиг/том үсэг, тоо, тусгай тэмдэгтийн дор хаяж 3 төрлийг агуулна."
    if password.strip().lower() in COMMON_PASSWORDS:
        return "Энэ нууц үг хэт түгээмэл байна. Өөр нууц үг сонгоно уу."
    return None


def hash_password(password: str) -> str:
    return ARGON2.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        if encoded.startswith("$argon2id$"):
            return bool(ARGON2.verify(encoded, password))
        if encoded.startswith("scrypt$"):
            _, n_raw, r_raw, p_raw, salt_hex, digest_hex = encoded.split("$", 5)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n_raw), r=int(r_raw), p=int(p_raw), dklen=len(bytes.fromhex(digest_hex)),
            )
            return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
        if encoded.startswith("pbkdf2_sha256$"):
            scheme, rounds_raw, salt_hex, digest_hex = encoded.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds_raw)
            )
            return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError, MemoryError, VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return False


def password_needs_rehash(encoded: str) -> bool:
    if not encoded.startswith("$argon2id$"):
        return True
    try:
        return ARGON2.check_needs_rehash(encoded)
    except (InvalidHashError, VerificationError):
        return True


def _client_ip(request: Request) -> str:
    # Deliberately ignore X-Forwarded-For by default. Reverse proxies should terminate TLS
    # and can be configured separately; blindly trusting this header enables spoofing.
    return request.client.host if request.client else "unknown"


def create_session(user_id: int, response: Response, request: Request) -> str:
    session_id = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=settings.session_hours)
    user_agent = (request.headers.get("user-agent") or "")[:500]
    ip_address = _client_ip(request)[:120]
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
        # Keep the newest N sessions per user to limit stolen/stale sessions.
        rows = conn.execute(
            "SELECT id FROM sessions WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        for old in rows[max(0, settings.max_sessions_per_user - 1):]:
            conn.execute("DELETE FROM sessions WHERE id=?", (old["id"],))
        conn.execute(
            """
            INSERT INTO sessions(id,user_id,csrf_token,expires_at,created_at,last_seen_at,ip_address,user_agent)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (session_id, user_id, csrf, expires.isoformat(timespec="seconds"), now_iso(), now_iso(), ip_address, user_agent),
        )
    response.set_cookie(
        SESSION_COOKIE,
        _signed_session_cookie(session_id),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )
    return csrf


def destroy_session(request: Request, response: Response) -> None:
    session_id = _parse_session_cookie(request.cookies.get(SESSION_COOKIE))
    if session_id:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    response.delete_cookie(SESSION_COOKIE, path="/")


def _user_from_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.id,u.username,u.name,u.active,
                   COALESCE(CASE WHEN d.active=1 THEN d.name ELSE '' END,'') AS department,
                   COALESCE(CASE WHEN r.active=1 THEN r.name ELSE '' END,'') AS role,
                   COALESCE(CASE WHEN r.active=1 THEN r.is_admin ELSE 0 END,0) AS is_admin,
                   s.id AS session_id,s.csrf_token,s.expires_at,s.last_seen_at
            FROM sessions s
            JOIN users u ON u.id=s.user_id
            LEFT JOIN departments d ON d.id=u.department_id
            LEFT JOIN roles r ON r.id=u.role_id
            WHERE s.id=?
            """,
            (session_id,),
        ).fetchone()
    if not row or not row["active"]:
        return None

    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        last_seen = datetime.fromisoformat(row["last_seen_at"] or row["expires_at"])
    except (TypeError, ValueError):
        return None

    idle_deadline = last_seen + timedelta(minutes=settings.session_idle_minutes)
    if expires < now or idle_deadline < now:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return None

    # Refresh last_seen at most once per minute to avoid excessive SQLite writes.
    if (now - last_seen).total_seconds() >= 60:
        with connect() as conn:
            conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now_iso(), session_id))

    return dict(row)


def current_session_id(request: Request) -> str | None:
    return _parse_session_cookie(request.cookies.get(SESSION_COOKIE))


def current_user(request: Request) -> dict[str, Any]:
    user = _user_from_session(current_session_id(request))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нэвтэрнэ үү.")
    return user


def optional_user(request: Request) -> dict[str, Any] | None:
    return _user_from_session(current_session_id(request))


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Админ эрх шаардлагатай.")
    return user


def require_csrf(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    token = request.headers.get(CSRF_HEADER, "")
    if not token or not hmac.compare_digest(token, user.get("csrf_token", "")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF баталгаажуулалт амжилтгүй.")
    return user


def require_admin_csrf(request: Request, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    token = request.headers.get(CSRF_HEADER, "")
    if not token or not hmac.compare_digest(token, user.get("csrf_token", "")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF баталгаажуулалт амжилтгүй.")
    return user



def _api_token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_api_token(user_id: int, device_name: str = "DUREM App") -> tuple[str, str]:
    if get_setting("api_access_enabled", "1") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="App API access админаар унтраалттай байна.")
    try:
        ttl_days = int(get_setting("api_token_ttl_days", "30"))
    except ValueError:
        ttl_days = 30
    ttl_days = max(1, min(ttl_days, 365))
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=ttl_days)
    raw_token = "durem_v1_" + secrets.token_urlsafe(36)
    token_id = "api_" + secrets.token_hex(12)
    token_hash = _api_token_hash(raw_token)
    device = (device_name or "DUREM App").strip()[:120] or "DUREM App"
    with connect() as conn:
        conn.execute("DELETE FROM api_tokens WHERE expires_at < ? OR revoked=1", (now_iso(),))
        # Bound active mobile/app sessions per user. Browser sessions are managed separately.
        active = conn.execute(
            "SELECT id FROM api_tokens WHERE user_id=? AND revoked=0 ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        for old in active[15:]:
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE id=?", (old["id"],))
        conn.execute(
            """INSERT INTO api_tokens(id,user_id,token_hash,device_name,created_at,expires_at,last_used_at,revoked)
               VALUES(?,?,?,?,?,?,?,0)""",
            (token_id, user_id, token_hash, device, now_iso(), expires.isoformat(timespec="seconds"), now_iso()),
        )
    return raw_token, expires.isoformat(timespec="seconds")


def _bearer_token(request: Request) -> str | None:
    header = (request.headers.get("authorization") or "").strip()
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token if token.startswith("durem_v1_") else None


def _user_from_api_token(raw_token: str | None) -> dict[str, Any] | None:
    if not raw_token or get_setting("api_access_enabled", "1") != "1":
        return None
    token_hash = _api_token_hash(raw_token)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.id,u.username,u.name,u.active,
                   COALESCE(CASE WHEN d.active=1 THEN d.name ELSE '' END,'') AS department,
                   COALESCE(CASE WHEN r.active=1 THEN r.name ELSE '' END,'') AS role,
                   COALESCE(CASE WHEN r.active=1 THEN r.is_admin ELSE 0 END,0) AS is_admin,
                   t.id AS api_token_id,t.expires_at,t.last_used_at,t.revoked,t.device_name
            FROM api_tokens t
            JOIN users u ON u.id=t.user_id
            LEFT JOIN departments d ON d.id=u.department_id
            LEFT JOIN roles r ON r.id=u.role_id
            WHERE t.token_hash=?
            """,
            (token_hash,),
        ).fetchone()
    if not row or not row["active"] or row["revoked"]:
        return None
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        last_used = datetime.fromisoformat(row["last_used_at"] or row["expires_at"])
    except (TypeError, ValueError):
        return None
    if expires < now:
        with connect() as conn:
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE id=?", (row["api_token_id"],))
        return None
    if (now - last_used).total_seconds() >= 60:
        with connect() as conn:
            conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (now_iso(), row["api_token_id"]))
    return dict(row)


def api_user(request: Request) -> dict[str, Any]:
    user = _user_from_api_token(_bearer_token(request))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid DUREM API bearer token шаардлагатай.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def revoke_api_token(request: Request) -> bool:
    raw = _bearer_token(request)
    if not raw:
        return False
    token_hash = _api_token_hash(raw)
    with connect() as conn:
        cursor = conn.execute("UPDATE api_tokens SET revoked=1 WHERE token_hash=? AND revoked=0", (token_hash,))
    return cursor.rowcount > 0

def ensure_default_admin() -> None:
    """Create a bootstrap admin only when no user exists.

    The setup script should supply DUREM_BOOTSTRAP_PASSWORD. If it is missing we
    generate a strong one-time password and store it in data/bootstrap_admin.txt.
    """
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count:
            return
        role = conn.execute("SELECT id FROM roles WHERE name='Админ'").fetchone()
        department = conn.execute("SELECT id FROM departments WHERE name='Ерөнхий'").fetchone()
        username = "admin"
        password = os.getenv("DUREM_BOOTSTRAP_PASSWORD", "")
        if password_policy_error(password):
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
            password = "".join(secrets.choice(alphabet) for _ in range(22))
            note = settings.data_dir / "bootstrap_admin.txt"
            note.write_text(
                f"DUREM first-run admin\nusername: {username}\npassword: {password}\n\nDelete this file after first login.\n",
                encoding="utf-8",
            )
            try:
                note.chmod(0o600)
            except OSError:
                pass
        stamp = now_iso()
        conn.execute(
            """INSERT INTO users(username,name,password_hash,department_id,role_id,active,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)""",
            (username, "DUREM Admin", hash_password(password), department["id"] if department else None, role["id"] if role else None, stamp, stamp),
        )
