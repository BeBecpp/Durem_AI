from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .assistant_engine import answer, preview_route
from .auth import (
    api_user,
    create_api_token,
    create_session,
    current_user,
    destroy_session,
    ensure_default_admin,
    hash_password,
    password_needs_rehash,
    password_policy_error,
    current_session_id,
    optional_user,
    require_admin,
    require_admin_csrf,
    require_csrf,
    revoke_api_token,
    verify_password,
)
from .backup import BackupError, create_backup, restore_backup
from .config import ROOT, settings
from .db import audit, connect, db_ok, get_setting, init_db, now_iso, rows_to_dicts, set_setting
from .documents import ingest_document, reindex_document
from .lemonade import LemonadeClient, LemonadeError
from .memory import forget_all, list_memories
from .models import (
    ApiLoginRequest,
    ApiTokenResponse,
    AskRequest,
    AssistantResponse,
    DepartmentInput,
    DocumentStatusInput,
    HealthResponse,
    LoginRequest,
    PasswordChangeInput,
    FeedbackInput,
    BackupInput,
    ResponsibilityInput,
    RoleInput,
    RoutePreviewRequest,
    RoutePreviewResponse,
    RuleInput,
    SettingInput,
    UserInput,
)
from .rate_limit import check_rate_limit, check_login_rate_limit, clear_login_rate_limit

STATIC = ROOT / "app" / "static"


def _validate_runtime_security() -> None:
    # LAN exposure must never rely on the developer defaults.
    if settings.network_exposed:
        if not settings.secret_is_strong:
            raise RuntimeError("LAN mode requires a strong DUREM_SECRET_KEY (48+ random characters).")
        if "*" in settings.allowed_hosts:
            raise RuntimeError("LAN mode requires explicit DUREM_ALLOWED_HOSTS; wildcard '*' is not allowed.")
    if not settings.allow_external_ai and not settings.llm_endpoint_is_local:
        raise RuntimeError(
            "DUREM is local-first: LEMONADE_BASE_URL must be loopback/private unless "
            "DUREM_ALLOW_EXTERNAL_AI=true is explicitly set."
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_runtime_security()
    init_db()
    ensure_default_admin()
    yield


app = FastAPI(
    title="DUREM Local AI",
    version=settings.version,
    description="Local-first company policy and decision assistant",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-DUREM-Version"] = settings.version
    if request.url.scheme == "https" or settings.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/") or request.url.path in {"/", "/login", "/admin"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"], "username": user["username"], "name": user["name"],
        "department": user.get("department", ""), "role": user.get("role", ""),
        "is_admin": bool(user.get("is_admin")),
    }


def _active_admin_count(conn, exclude_user_id: int | None = None, exclude_role_id: int | None = None) -> int:
    where = ["u.active=1", "r.active=1", "r.is_admin=1"]
    args: list[Any] = []
    if exclude_user_id is not None:
        where.append("u.id<>?")
        args.append(exclude_user_id)
    if exclude_role_id is not None:
        where.append("r.id<>?")
        args.append(exclude_role_id)
    row = conn.execute(
        f"SELECT COUNT(*) c FROM users u JOIN roles r ON r.id=u.role_id WHERE {' AND '.join(where)}",
        tuple(args),
    ).fetchone()
    return int(row["c"] if row else 0)


def _would_remove_last_admin(conn, user_id: int, new_role_id: int | None, new_active: bool) -> bool:
    current = conn.execute(
        "SELECT u.active,COALESCE(r.is_admin,0) is_admin FROM users u LEFT JOIN roles r ON r.id=u.role_id WHERE u.id=?",
        (user_id,),
    ).fetchone()
    if not current or not current["active"] or not current["is_admin"]:
        return False
    new_role = conn.execute("SELECT active,is_admin FROM roles WHERE id=?", (new_role_id,)).fetchone() if new_role_id else None
    remains_admin = bool(new_active and new_role and new_role["active"] and new_role["is_admin"])
    return not remains_admin and _active_admin_count(conn, exclude_user_id=user_id) == 0


def _validate_date_range(effective_from: str, effective_to: str) -> tuple[str, str]:
    values = []
    for label, raw in (("Effective from", effective_from), ("Effective to", effective_to)):
        value = raw.strip()
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{label} YYYY-MM-DD форматтай байна.") from exc
        values.append(value)
    start, end = values
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="Effective to нь effective from-оос өмнө байж болохгүй.")
    return start, end


def _bounded_form(value: str, label: str, max_length: int, default: str = "") -> str:
    cleaned = value.strip() or default
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"{label} хэт урт байна (max {max_length}).")
    return cleaned


def _document_is_available_to_user(row: Any, user: dict[str, Any]) -> bool:
    if user.get("is_admin"):
        return True
    today = date.today().isoformat()
    if row["status"] != "active":
        return False
    if row["effective_from"] and row["effective_from"] > today:
        return False
    if row["effective_to"] and row["effective_to"] < today:
        return False
    return row["visibility"] == "all" or (
        row["visibility"] == "department" and row["department"] == user.get("department")
    )


@app.get("/", include_in_schema=False)
def home(request: Request):
    if not optional_user(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC / "index.html")


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if optional_user(request):
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC / "login.html")


@app.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    user = optional_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user.get("is_admin"):
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC / "admin.html")


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response, request: Request) -> dict[str, Any]:
    login_key = check_login_rate_limit(request, payload.username)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.*,
                   COALESCE(CASE WHEN d.active=1 THEN d.name ELSE '' END,'') department,
                   COALESCE(CASE WHEN r.active=1 THEN r.name ELSE '' END,'') role,
                   COALESCE(CASE WHEN r.active=1 THEN r.is_admin ELSE 0 END,0) is_admin
            FROM users u
            LEFT JOIN departments d ON d.id=u.department_id
            LEFT JOIN roles r ON r.id=u.role_id
            WHERE u.username=? COLLATE NOCASE
            """,
            (payload.username.strip(),),
        ).fetchone()
    ip = request.client.host if request.client else "unknown"
    if not row or not row["active"] or not verify_password(payload.password, row["password_hash"]):
        audit(row["id"] if row else None, "auth", "login_failed", {"username": payload.username, "ip": ip})
        raise HTTPException(status_code=401, detail="Нэвтрэх нэр эсвэл нууц үг буруу байна.")
    user = dict(row)
    if password_needs_rehash(user["password_hash"]):
        with connect() as conn:
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(payload.password), now_iso(), user["id"]))
    csrf = create_session(user["id"], response, request)
    clear_login_rate_limit(login_key)
    audit(user["id"], "auth", "login", {"ip": ip})
    return {"user": _public_user(user), "csrf_token": csrf}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
    audit(user["id"], "auth", "logout", {})
    destroy_session(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": _public_user(user), "csrf_token": user["csrf_token"]}


@app.post("/api/auth/change-password")
def change_password(payload: PasswordChangeInput, request: Request, response: Response, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, Any]:
    error = password_policy_error(payload.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Одоогийн нууц үг буруу байна.")
        conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(payload.new_password), now_iso(), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user["id"],))
    response.delete_cookie("durem_session", path="/")
    audit(user["id"], "auth", "password_changed", {})
    return {"ok": True, "logout_required": True}


# ------------------------- Versioned app API -------------------------
@app.get("/api/v1/meta")
def api_v1_meta() -> dict[str, Any]:
    return {
        "api_version": "v1",
        "app_version": settings.version,
        "auth": "bearer",
        "api_access_enabled": get_setting("api_access_enabled", "1") == "1",
        "routes": ["chat", "policy"],
        "modes": ["auto", "chat", "policy", "can_i", "how_to", "who"],
    }


@app.post("/api/v1/auth/login", response_model=ApiTokenResponse)
def api_v1_login(payload: ApiLoginRequest, request: Request) -> ApiTokenResponse:
    if get_setting("api_access_enabled", "1") != "1":
        raise HTTPException(status_code=403, detail="App API access админаар унтраалттай байна.")
    login_key = check_login_rate_limit(request, payload.username)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.*,
                   COALESCE(CASE WHEN d.active=1 THEN d.name ELSE '' END,'') department,
                   COALESCE(CASE WHEN r.active=1 THEN r.name ELSE '' END,'') role,
                   COALESCE(CASE WHEN r.active=1 THEN r.is_admin ELSE 0 END,0) is_admin
            FROM users u
            LEFT JOIN departments d ON d.id=u.department_id
            LEFT JOIN roles r ON r.id=u.role_id
            WHERE u.username=? COLLATE NOCASE
            """,
            (payload.username.strip(),),
        ).fetchone()
    ip = request.client.host if request.client else "unknown"
    if not row or not row["active"] or not verify_password(payload.password, row["password_hash"]):
        audit(row["id"] if row else None, "auth", "api_login_failed", {"username": payload.username, "ip": ip})
        raise HTTPException(status_code=401, detail="Нэвтрэх нэр эсвэл нууц үг буруу байна.")
    user = dict(row)
    if password_needs_rehash(user["password_hash"]):
        with connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                (hash_password(payload.password), now_iso(), user["id"]),
            )
    raw_token, expires_at = create_api_token(int(user["id"]), payload.device_name)
    clear_login_rate_limit(login_key)
    audit(user["id"], "auth", "api_login", {"ip": ip, "device_name": payload.device_name[:120]})
    return ApiTokenResponse(access_token=raw_token, expires_at=expires_at, user=_public_user(user))


@app.get("/api/v1/auth/me")
def api_v1_me(user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    return {"user": _public_user(user), "api_version": "v1", "device_name": user.get("device_name", "")}


@app.delete("/api/v1/auth/session")
def api_v1_logout(request: Request, user: dict[str, Any] = Depends(api_user)) -> dict[str, bool]:
    revoked = revoke_api_token(request)
    audit(user["id"], "auth", "api_logout", {"token_id": str(user.get("api_token_id", ""))[:16]})
    return {"revoked": revoked}


@app.post("/api/v1/auth/change-password")
def api_v1_change_password(payload: PasswordChangeInput, user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    error = password_policy_error(payload.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Одоогийн нууц үг буруу байна.")
        conn.execute(
            "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
            (hash_password(payload.new_password), now_iso(), user["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user["id"],))
    audit(user["id"], "auth", "api_password_changed", {})
    return {"ok": True, "logout_required": True}


@app.get("/api/v1/auth/sessions")
def api_v1_sessions(user: dict[str, Any] = Depends(api_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id,device_name,created_at,last_used_at,expires_at
               FROM api_tokens WHERE user_id=? AND revoked=0 AND expires_at>=?
               ORDER BY last_used_at DESC""",
            (user["id"], now_iso()),
        ).fetchall()
    current = str(user.get("api_token_id", ""))
    output = rows_to_dicts(rows)
    for item in output:
        item["current"] = item["id"] == current
    return output


@app.delete("/api/v1/auth/sessions/{token_id}")
def api_v1_revoke_session(token_id: str, user: dict[str, Any] = Depends(api_user)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE api_tokens SET revoked=1 WHERE id=? AND user_id=? AND revoked=0",
            (token_id, user["id"]),
        )
    audit(user["id"], "auth", "api_session_revoked", {"token_id": token_id[:18]})
    return {"revoked": cursor.rowcount > 0}


@app.get("/api/v1/config")
def api_v1_config(user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    return {
        "api_version": "v1",
        "company": get_setting("company_name", settings.company_name),
        "model": get_setting("llm_model", settings.llm_model),
        "general_chat_enabled": get_setting("general_chat_enabled", "1") == "1",
        "auto_routing_enabled": get_setting("auto_routing_enabled", "1") == "1",
        "hybrid_router_enabled": get_setting("hybrid_router_enabled", "1") == "1",
        "personal_memory_enabled": get_setting("personal_memory_enabled", "1") == "1",
        "version": settings.version,
        "user": _public_user(user),
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def api_v1_health(user: dict[str, Any] = Depends(api_user)) -> HealthResponse:
    reachable, _models = await LemonadeClient().health()
    return HealthResponse(
        status="ok" if db_ok() and (reachable or settings.mock_mode) else "degraded",
        database=db_ok(), llm_reachable=reachable or settings.mock_mode,
        llm_model=get_setting("llm_model", settings.llm_model),
        embedding_model=get_setting("embedding_model", settings.embedding_model),
        embeddings_enabled=get_setting("embeddings_enabled", "1") == "1",
        company=get_setting("company_name", settings.company_name), version=settings.version,
    )


@app.post("/api/v1/assistant/route", response_model=RoutePreviewResponse)
async def api_v1_route(payload: RoutePreviewRequest, user: dict[str, Any] = Depends(api_user)) -> RoutePreviewResponse:
    check_rate_limit(int(user["id"]))
    request = AskRequest(**payload.model_dump())
    decision = await preview_route(request, user)
    return RoutePreviewResponse(
        route=decision.route,
        requested_mode=payload.mode,
        route_reason=decision.reason,
        route_confidence=decision.confidence,
        route_method=decision.method,
        safety_override=decision.safety_override,
        classifier_invoked=decision.classifier_invoked,
        signals=list(decision.signals),
    )


@app.post("/api/v1/assistant/ask", response_model=AssistantResponse)
async def api_v1_ask(payload: AskRequest, user: dict[str, Any] = Depends(api_user)) -> AssistantResponse:
    check_rate_limit(int(user["id"]))
    try:
        return await answer(payload, user)
    except LemonadeError as exc:
        audit(user["id"], "assistant", "model_error", {"error": str(exc)[:800], "api": "v1"})
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/conversations")
def api_v1_conversations(user: dict[str, Any] = Depends(api_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/v1/conversations/{conversation_id}")
def api_v1_conversation(conversation_id: str, user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    with connect() as conn:
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["id"])
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Яриа олдсонгүй.")
        messages = conn.execute(
            "SELECT id,role,content,response_json,created_at FROM messages WHERE conversation_id=? ORDER BY created_at,rowid",
            (conversation_id,),
        ).fetchall()
    parsed: list[dict[str, Any]] = []
    for row in messages:
        item = dict(row)
        try:
            item["response"] = json.loads(item.pop("response_json")) if item.get("response_json") else None
        except json.JSONDecodeError:
            item["response"] = None
            item.pop("response_json", None)
        parsed.append(item)
    return {"conversation": dict(conv), "messages": parsed}


@app.delete("/api/v1/conversations/{conversation_id}")
def api_v1_delete_conversation(conversation_id: str, user: dict[str, Any] = Depends(api_user)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["id"])
        )
    return {"deleted": cursor.rowcount > 0}


@app.get("/api/v1/memory")
def api_v1_memory(user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    return {
        "enabled": get_setting("personal_memory_enabled", "1") == "1",
        "items": list_memories(int(user["id"]), limit=50),
    }


@app.delete("/api/v1/memory")
def api_v1_clear_memory(user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    count = forget_all(int(user["id"]))
    audit(user["id"], "memory", "api_memory_clear", {"changed": count})
    return {"cleared": count}


@app.post("/api/v1/feedback")
def api_v1_feedback(payload: FeedbackInput, user: dict[str, Any] = Depends(api_user)) -> dict[str, bool]:
    with connect() as conn:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (payload.conversation_id, user["id"]),
        ).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Яриа олдсонгүй.")
        conn.execute(
            "INSERT INTO message_feedback(conversation_id,user_id,assistant_message_id,rating,note,created_at) VALUES(?,?,?,?,?,?)",
            (payload.conversation_id, user["id"], payload.assistant_message_id or None, payload.rating, payload.note.strip(), now_iso()),
        )
    audit(user["id"], "assistant", "api_feedback", {"conversation_id": payload.conversation_id, "rating": payload.rating})
    return {"ok": True}


@app.get("/api/v1/documents/{document_id}/file")
def api_v1_document_file(document_id: str, user: dict[str, Any] = Depends(api_user)):
    with connect() as conn:
        row = conn.execute(
            """SELECT d.*,COALESCE(dep.name,'') department FROM documents d
               LEFT JOIN departments dep ON dep.id=d.department_id WHERE d.id=?""",
            (document_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Баримт олдсонгүй.")
    if not _document_is_available_to_user(row, user):
        raise HTTPException(status_code=403, detail="Энэ баримтыг харах эрхгүй эсвэл баримт одоогоор хүчинтэй биш байна.")
    path = settings.documents_dir / row["stored_name"]
    return FileResponse(path, filename=row["filename"], content_disposition_type="attachment")


@app.get("/api/v1/documents/{document_id}/preview")
def api_v1_document_preview(document_id: str, user: dict[str, Any] = Depends(api_user)) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """SELECT d.*,COALESCE(dep.name,'') department FROM documents d
               LEFT JOIN departments dep ON dep.id=d.department_id WHERE d.id=?""",
            (document_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Баримт олдсонгүй.")
        if not _document_is_available_to_user(row, user):
            raise HTTPException(status_code=403, detail="Энэ баримтыг харах эрхгүй эсвэл баримт одоогоор хүчинтэй биш байна.")
        chunks = conn.execute(
            "SELECT section,content FROM document_chunks WHERE document_id=? ORDER BY chunk_index LIMIT 12",
            (document_id,),
        ).fetchall()
    return {
        "id": row["id"], "title": row["title"], "filename": row["filename"], "version": row["version"],
        "category": row["category"], "status": row["status"], "effective_from": row["effective_from"],
        "effective_to": row["effective_to"], "chunks": [dict(item) for item in chunks],
    }


@app.get("/api/config")
def config(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {
        "company": get_setting("company_name", settings.company_name),
        "model": get_setting("llm_model", settings.llm_model),
        "embedding_model": get_setting("embedding_model", settings.embedding_model),
        "embeddings_enabled": get_setting("embeddings_enabled", "1") == "1",
        "general_chat_enabled": get_setting("general_chat_enabled", "1") == "1",
        "auto_routing_enabled": get_setting("auto_routing_enabled", "1") == "1",
        "hybrid_router_enabled": get_setting("hybrid_router_enabled", "1") == "1",
        "personal_memory_enabled": get_setting("personal_memory_enabled", "1") == "1",
        "api_access_enabled": get_setting("api_access_enabled", "1") == "1",
        "api_token_ttl_days": int(get_setting("api_token_ttl_days", "30")),
        "chat_history_messages": int(get_setting("chat_history_messages", "16")),
        "version": settings.version,
        "user": _public_user(user),
    }


@app.get("/api/health", response_model=HealthResponse)
async def health(user: dict[str, Any] = Depends(current_user)) -> HealthResponse:
    reachable, models = await LemonadeClient().health()
    llm_model = get_setting("llm_model", settings.llm_model)
    embed_model = get_setting("embedding_model", settings.embedding_model)
    return HealthResponse(
        status="ok" if db_ok() and (reachable or settings.mock_mode) else "degraded",
        database=db_ok(), llm_reachable=reachable or settings.mock_mode,
        llm_model=llm_model, embedding_model=embed_model,
        embeddings_enabled=get_setting("embeddings_enabled", "1") == "1",
        company=get_setting("company_name", settings.company_name), version=settings.version,
    )


@app.post("/api/ask", response_model=AssistantResponse)
async def ask(payload: AskRequest, user: dict[str, Any] = Depends(require_csrf)) -> AssistantResponse:
    check_rate_limit(int(user["id"]))
    try:
        return await answer(payload, user)
    except LemonadeError as exc:
        audit(user["id"], "assistant", "model_error", {"error": str(exc)[:800]})
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/conversations")
def conversations(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,title,created_at,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as conn:
        conv = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["id"])).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Яриа олдсонгүй.")
        messages = conn.execute(
            "SELECT id,role,content,response_json,created_at FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
    parsed = []
    for row in messages:
        item = dict(row)
        item["response"] = json.loads(item.pop("response_json")) if item.get("response_json") else None
        parsed.append(item)
    return {"conversation": dict(conv), "messages": parsed}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["id"]))
    return {"deleted": cursor.rowcount > 0}


# ------------------------- Admin dashboard -------------------------
@app.get("/api/admin/stats")
def admin_stats(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with connect() as conn:
        stats = {
            "users": conn.execute("SELECT COUNT(*) c FROM users WHERE active=1").fetchone()["c"],
            "documents": conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active'").fetchone()["c"],
            "rules": conn.execute("SELECT COUNT(*) c FROM rules WHERE active=1").fetchone()["c"],
            "questions_today": conn.execute("SELECT COUNT(*) c FROM audit_logs WHERE event_type='assistant' AND action='answer' AND created_at >= date('now')").fetchone()["c"],
            "not_found": conn.execute("SELECT COUNT(*) c FROM audit_logs WHERE event_type='assistant' AND action='answer' AND metadata_json LIKE '%NOT_FOUND%' AND created_at >= datetime('now','-7 days')").fetchone()["c"],
        }
        recent = rows_to_dicts(conn.execute(
            """
            SELECT a.id,a.event_type,a.action,a.metadata_json,a.created_at,COALESCE(u.name,'System') user_name
            FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
            ORDER BY a.id DESC LIMIT 12
            """
        ).fetchall())
    for item in recent:
        try:
            item["metadata"] = json.loads(item.pop("metadata_json"))
        except json.JSONDecodeError:
            item["metadata"] = {}
    return {"stats": stats, "recent": recent}


@app.get("/api/admin/knowledge-health")
def knowledge_health(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with connect() as conn:
        active_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active'").fetchone()["c"]
        archived_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='archived'").fetchone()["c"]
        hybrid_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active' AND index_mode='hybrid'").fetchone()["c"]
        lexical_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active' AND index_mode<>'hybrid'").fetchone()["c"]
        zero_chunks = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active' AND chunk_count=0").fetchone()["c"]
        expired_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active' AND effective_to<>'' AND effective_to < date('now')").fetchone()["c"]
        future_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='active' AND effective_from<>'' AND effective_from > date('now')").fetchone()["c"]
        active_rules = conn.execute("SELECT COUNT(*) c FROM rules WHERE active=1").fetchone()["c"]
        manual_rules = conn.execute("SELECT COUNT(*) c FROM rules WHERE active=1 AND (source_document_id IS NULL OR source_document_id='')").fetchone()["c"]
        unavailable_rule_sources = conn.execute(
            """SELECT COUNT(*) c FROM rules r LEFT JOIN documents d ON d.id=r.source_document_id
               WHERE r.active=1 AND r.source_document_id IS NOT NULL AND r.source_document_id<>''
                 AND (d.id IS NULL OR d.status<>'active' OR (d.effective_from<>'' AND d.effective_from > date('now')) OR (d.effective_to<>'' AND d.effective_to < date('now')))"""
        ).fetchone()["c"]
        duplicate_titles = rows_to_dicts(conn.execute(
            """SELECT title,COUNT(*) count FROM documents WHERE status='active'
               GROUP BY lower(trim(title)) HAVING COUNT(*)>1 ORDER BY count DESC,title LIMIT 20"""
        ).fetchall())
    issues = []
    if zero_chunks:
        issues.append({"level":"danger","title":"Index хоосон баримт","detail":f"{zero_chunks} active document 0 chunk-тэй байна."})
    if unavailable_rule_sources:
        issues.append({"level":"danger","title":"Дүрмийн эх сурвалж идэвхгүй","detail":f"{unavailable_rule_sources} active rule archived/expired source-той тул employee retrieval-д ашиглагдахгүй."})
    if duplicate_titles:
        issues.append({"level":"warning","title":"Олон active version","detail":f"{len(duplicate_titles)} гарчиг дээр давхардсан active version байна."})
    if expired_docs:
        issues.append({"level":"warning","title":"Хугацаа дууссан active баримт","detail":f"{expired_docs} document status=active боловч effective_to өнгөрсөн."})
    if lexical_docs and get_setting("embeddings_enabled", "1") == "1":
        issues.append({"level":"info","title":"Lexical-only index","detail":f"{lexical_docs} active document semantic embedding-гүй байна. Reindex хийж болно."})
    if not active_docs and not active_rules:
        issues.append({"level":"info","title":"Knowledge base хоосон","detail":"Албан ёсны баримт эсвэл дүрмээ upload/add хийж эхэлнэ үү."})
    penalty = sum({"danger":20,"warning":10,"info":4}.get(item["level"],0) for item in issues)
    score = max(0, 100 - penalty)
    return {
        "score": score,
        "active_documents": active_docs, "archived_documents": archived_docs,
        "hybrid_documents": hybrid_docs, "lexical_documents": lexical_docs,
        "future_documents": future_docs, "active_rules": active_rules, "manual_rules": manual_rules,
        "issues": issues, "duplicate_titles": duplicate_titles,
    }


# ------------------------- Organization -------------------------
@app.get("/api/admin/departments")
def list_departments(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM departments ORDER BY active DESC,name").fetchall())


@app.post("/api/admin/departments")
def create_department(payload: DepartmentInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    try:
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO departments(name,description,active,created_at) VALUES(?,?,?,?)",
                (payload.name.strip(), payload.description.strip(), int(payload.active), now_iso()),
            )
            item_id = cursor.lastrowid
        audit(admin["id"], "admin", "department_created", {"id": item_id, "name": payload.name})
        return {"id": item_id, **payload.model_dump()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Ижил нэртэй хэлтэс байж магадгүй.") from exc


@app.put("/api/admin/departments/{item_id}")
def update_department(item_id: int, payload: DepartmentInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("UPDATE departments SET name=?,description=?,active=? WHERE id=?", (payload.name.strip(), payload.description.strip(), int(payload.active), item_id))
    audit(admin["id"], "admin", "department_updated", {"id": item_id})
    return {"id": item_id, **payload.model_dump()}


@app.get("/api/admin/roles")
def list_roles(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM roles ORDER BY is_admin DESC,active DESC,name").fetchall())


@app.post("/api/admin/roles")
def create_role(payload: RoleInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    try:
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO roles(name,description,is_admin,active,created_at) VALUES(?,?,?,?,?)",
                (payload.name.strip(), payload.description.strip(), int(payload.is_admin), int(payload.active), now_iso()),
            )
        audit(admin["id"], "admin", "role_created", {"id": cursor.lastrowid, "name": payload.name})
        return {"id": cursor.lastrowid, **payload.model_dump()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Ижил нэртэй role байж магадгүй.") from exc


@app.put("/api/admin/roles/{item_id}")
def update_role(item_id: int, payload: RoleInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    try:
        with connect() as conn:
            current = conn.execute("SELECT id,is_admin,active FROM roles WHERE id=?", (item_id,)).fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Role олдсонгүй.")
            if current["is_admin"] and current["active"] and (not payload.is_admin or not payload.active):
                assigned = conn.execute("SELECT COUNT(*) c FROM users WHERE active=1 AND role_id=?", (item_id,)).fetchone()["c"]
                if assigned and _active_admin_count(conn, exclude_role_id=item_id) == 0:
                    raise HTTPException(status_code=400, detail="Системийн сүүлийн идэвхтэй админ role-ийг идэвхгүй болгох боломжгүй.")
            conn.execute(
                "UPDATE roles SET name=?,description=?,is_admin=?,active=? WHERE id=?",
                (payload.name.strip(), payload.description.strip(), int(payload.is_admin), int(payload.active), item_id),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Role-ийн мэдээлэл буруу эсвэл нэр давхардсан байна.") from exc
    audit(admin["id"], "admin", "role_updated", {"id": item_id})
    return {"id": item_id, **payload.model_dump()}


@app.get("/api/admin/users")
def list_users(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id,u.username,u.name,u.department_id,u.role_id,u.active,u.created_at,
                   COALESCE(d.name,'') department,COALESCE(r.name,'') role,COALESCE(r.is_admin,0) is_admin
            FROM users u LEFT JOIN departments d ON d.id=u.department_id LEFT JOIN roles r ON r.id=u.role_id
            ORDER BY u.active DESC,u.name
            """
        ).fetchall()
    return rows_to_dicts(rows)


@app.post("/api/admin/users")
def create_user(payload: UserInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    error = password_policy_error(payload.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    try:
        stamp = now_iso()
        with connect() as conn:
            cursor = conn.execute(
                """INSERT INTO users(username,name,password_hash,department_id,role_id,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (payload.username.strip(), payload.name.strip(), hash_password(payload.password), payload.department_id, payload.role_id, int(payload.active), stamp, stamp),
            )
        audit(admin["id"], "admin", "user_created", {"id": cursor.lastrowid, "username": payload.username})
        return {"id": cursor.lastrowid, "username": payload.username, "name": payload.name}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Username давхардсан эсвэл мэдээлэл буруу байна.") from exc


@app.put("/api/admin/users/{item_id}")
def update_user(item_id: int, payload: UserInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    try:
        with connect() as conn:
            if not conn.execute("SELECT id FROM users WHERE id=?", (item_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Хэрэглэгч олдсонгүй.")
            if _would_remove_last_admin(conn, item_id, payload.role_id, payload.active):
                raise HTTPException(status_code=400, detail="Системд дор хаяж нэг идэвхтэй админ үлдэх ёстой.")
            if payload.password:
                error = password_policy_error(payload.password)
                if error:
                    raise HTTPException(status_code=400, detail=error)
                conn.execute(
                    "UPDATE users SET username=?,name=?,password_hash=?,department_id=?,role_id=?,active=?,updated_at=? WHERE id=?",
                    (payload.username.strip(), payload.name.strip(), hash_password(payload.password), payload.department_id, payload.role_id, int(payload.active), now_iso(), item_id),
                )
                # Credential reset invalidates the user's existing sessions.
                conn.execute("DELETE FROM sessions WHERE user_id=?", (item_id,))
                conn.execute("DELETE FROM api_tokens WHERE user_id=?", (item_id,))
            else:
                conn.execute(
                    "UPDATE users SET username=?,name=?,department_id=?,role_id=?,active=?,updated_at=? WHERE id=?",
                    (payload.username.strip(), payload.name.strip(), payload.department_id, payload.role_id, int(payload.active), now_iso(), item_id),
                )
                if not payload.active:
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (item_id,))
                conn.execute("DELETE FROM api_tokens WHERE user_id=?", (item_id,))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Хэрэглэгчийн мэдээлэл буруу эсвэл username давхардсан байна.") from exc
    audit(admin["id"], "admin", "user_updated", {"id": item_id})
    return {"id": item_id, "username": payload.username, "name": payload.name}


# ------------------------- Rules -------------------------
@app.get("/api/admin/rules")
def list_rules(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM rules ORDER BY active DESC,priority DESC,title").fetchall())


@app.post("/api/admin/rules")
def save_rule(payload: RuleInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO rules(id,title,text,category,keywords,decision_hint,approver,role_scope,department_scope,priority,metric,min_value,max_value,min_inclusive,max_inclusive,source_document_id,source_section,active,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,category=excluded.category,
              keywords=excluded.keywords,decision_hint=excluded.decision_hint,approver=excluded.approver,
              role_scope=excluded.role_scope,department_scope=excluded.department_scope,priority=excluded.priority,
              metric=excluded.metric,min_value=excluded.min_value,max_value=excluded.max_value,
              min_inclusive=excluded.min_inclusive,max_inclusive=excluded.max_inclusive,
              source_document_id=excluded.source_document_id,source_section=excluded.source_section,
              active=excluded.active,updated_at=excluded.updated_at
            """,
            (
                payload.id,payload.title,payload.text,payload.category,payload.keywords,payload.decision_hint,
                payload.approver,payload.role_scope,payload.department_scope,payload.priority,payload.metric,
                payload.min_value,payload.max_value,int(payload.min_inclusive),int(payload.max_inclusive),
                payload.source_document_id or None,payload.source_section,int(payload.active),stamp,stamp,
            ),
        )
    audit(admin["id"], "admin", "rule_saved", {"id": payload.id})
    return payload.model_dump()


@app.delete("/api/admin/rules/{rule_id}")
def delete_rule(rule_id: str, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    audit(admin["id"], "admin", "rule_deleted", {"id": rule_id})
    return {"deleted": cursor.rowcount > 0}


# ------------------------- Responsibilities -------------------------
@app.get("/api/admin/responsibilities")
def list_responsibilities(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT x.*,COALESCE(d.name,'') department,COALESCE(u.name,'') user_name,COALESCE(r.name,'') role
            FROM responsibilities x
            LEFT JOIN departments d ON d.id=x.department_id LEFT JOIN users u ON u.id=x.user_id LEFT JOIN roles r ON r.id=x.role_id
            ORDER BY x.active DESC,x.topic
            """
        ).fetchall()
    return rows_to_dicts(rows)


@app.post("/api/admin/responsibilities")
def save_responsibility(payload: ResponsibilityInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO responsibilities(id,topic,keywords,department_id,user_id,role_id,instructions,active,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET topic=excluded.topic,keywords=excluded.keywords,department_id=excluded.department_id,
              user_id=excluded.user_id,role_id=excluded.role_id,instructions=excluded.instructions,active=excluded.active,updated_at=excluded.updated_at
            """,
            (payload.id,payload.topic,payload.keywords,payload.department_id,payload.user_id,payload.role_id,payload.instructions,int(payload.active),stamp,stamp),
        )
    audit(admin["id"], "admin", "responsibility_saved", {"id": payload.id})
    return payload.model_dump()


@app.delete("/api/admin/responsibilities/{item_id}")
def delete_responsibility(item_id: str, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM responsibilities WHERE id=?", (item_id,))
    return {"deleted": cursor.rowcount > 0}


# ------------------------- Documents -------------------------
@app.get("/api/admin/documents")
def list_documents(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.*,COALESCE(dep.name,'') department,COALESCE(u.name,'') created_by_name
            FROM documents d LEFT JOIN departments dep ON dep.id=d.department_id LEFT JOIN users u ON u.id=d.created_by
            ORDER BY d.status='active' DESC,d.updated_at DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


@app.post("/api/admin/documents/upload")
async def upload_document(
    file: UploadFile = File(...), title: str = Form(""), category: str = Form("general"),
    visibility: str = Form("all"), department_id: str = Form(""), version: str = Form("1.0"),
    effective_from: str = Form(""), effective_to: str = Form(""), archive_previous: str = Form("true"),
    admin: dict[str, Any] = Depends(require_admin_csrf),
) -> dict[str, Any]:
    if visibility not in {"all", "department", "admin"}:
        raise HTTPException(status_code=400, detail="Visibility буруу байна.")
    raw = await file.read(settings.upload_max_mb * 1024 * 1024 + 1)
    dep_id = int(department_id) if department_id.strip().isdigit() else None
    clean_from, clean_to = _validate_date_range(effective_from, effective_to)
    clean_title = _bounded_form(title or (file.filename or "Document"), "Гарчиг", 220, "Document")
    clean_category = _bounded_form(category, "Category", 80, "general")
    clean_version = _bounded_form(version, "Version", 80, "1.0")
    if visibility == "department" and dep_id is None:
        raise HTTPException(status_code=400, detail="Department visibility сонгосон бол хэлтэс заавал сонгоно.")
    if dep_id is not None:
        with connect() as conn:
            if not conn.execute("SELECT id FROM departments WHERE id=?", (dep_id,)).fetchone():
                raise HTTPException(status_code=400, detail="Сонгосон хэлтэс олдсонгүй.")
    try:
        result = await ingest_document(
            file_bytes=raw, filename=file.filename or "document", title=clean_title,
            category=clean_category, visibility=visibility, department_id=dep_id, version=clean_version,
            effective_from=clean_from, effective_to=clean_to, user_id=admin["id"],
            embeddings_enabled=get_setting("embeddings_enabled", "1") == "1",
        )
        archived_previous = 0
        if archive_previous.strip().lower() in {"1", "true", "yes", "on"}:
            with connect() as conn:
                cursor = conn.execute(
                    """UPDATE documents SET status='archived',updated_at=?
                       WHERE id<>? AND status='active' AND lower(trim(title))=lower(trim(?))""",
                    (now_iso(), result["id"], clean_title),
                )
                archived_previous = cursor.rowcount
        result["archived_previous"] = archived_previous
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(admin["id"], "admin", "document_uploaded", result)
    return result


@app.post("/api/admin/documents/{document_id}/reindex")
async def reindex(document_id: str, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    try:
        result = await reindex_document(document_id, get_setting("embeddings_enabled", "1") == "1")
    except (ValueError, LemonadeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(admin["id"], "admin", "document_reindexed", result)
    return result


@app.patch("/api/admin/documents/{document_id}/status")
def set_document_status(
    document_id: str, payload: DocumentStatusInput, admin: dict[str, Any] = Depends(require_admin_csrf)
) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT id,title,status FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Баримт олдсонгүй.")
        conn.execute("UPDATE documents SET status=?,updated_at=? WHERE id=?", (payload.status, now_iso(), document_id))
    result = {"id": document_id, "status": payload.status}
    audit(admin["id"], "admin", "document_status_changed", result)
    return result


@app.delete("/api/admin/documents/{document_id}")
def delete_document(document_id: str, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, bool]:
    with connect() as conn:
        row = conn.execute("SELECT stored_name FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            return {"deleted": False}
        conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
    (settings.documents_dir / row["stored_name"]).unlink(missing_ok=True)
    audit(admin["id"], "admin", "document_deleted", {"id": document_id})
    return {"deleted": True}


@app.get("/api/documents/{document_id}/file")
def document_file(document_id: str, user: dict[str, Any] = Depends(current_user)):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*,COALESCE(dep.name,'') department FROM documents d
            LEFT JOIN departments dep ON dep.id=d.department_id WHERE d.id=?
            """, (document_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Баримт олдсонгүй.")
    if not _document_is_available_to_user(row, user):
        raise HTTPException(status_code=403, detail="Энэ баримтыг харах эрхгүй эсвэл баримт одоогоор хүчинтэй биш байна.")
    path = settings.documents_dir / row["stored_name"]
    return FileResponse(path, filename=row["filename"], content_disposition_type="attachment")


@app.get("/api/documents/{document_id}/preview")
def document_preview(document_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """SELECT d.*,COALESCE(dep.name,'') department FROM documents d
               LEFT JOIN departments dep ON dep.id=d.department_id WHERE d.id=?""", (document_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Баримт олдсонгүй.")
        if not _document_is_available_to_user(row, user):
            raise HTTPException(status_code=403, detail="Энэ баримтыг харах эрхгүй эсвэл баримт одоогоор хүчинтэй биш байна.")
        chunks = conn.execute(
            "SELECT section,content FROM document_chunks WHERE document_id=? ORDER BY chunk_index LIMIT 12", (document_id,)
        ).fetchall()
    return {
        "id": row["id"], "title": row["title"], "filename": row["filename"], "version": row["version"],
        "category": row["category"], "status": row["status"], "effective_from": row["effective_from"],
        "effective_to": row["effective_to"], "chunks": [dict(item) for item in chunks],
    }


@app.post("/api/feedback")
def feedback(payload: FeedbackInput, user: dict[str, Any] = Depends(require_csrf)) -> dict[str, bool]:
    with connect() as conn:
        conv = conn.execute("SELECT id FROM conversations WHERE id=? AND user_id=?", (payload.conversation_id, user["id"])).fetchone()
        if not conv:
            raise HTTPException(status_code=404, detail="Яриа олдсонгүй.")
        conn.execute(
            "INSERT INTO message_feedback(conversation_id,user_id,assistant_message_id,rating,note,created_at) VALUES(?,?,?,?,?,?)",
            (payload.conversation_id, user["id"], payload.assistant_message_id or None, payload.rating, payload.note.strip(), now_iso()),
        )
    audit(user["id"], "assistant", "feedback", {"conversation_id": payload.conversation_id, "rating": payload.rating})
    return {"ok": True}


# ------------------------- Audit / settings / backup -------------------------
@app.get("/api/admin/audit")
def audit_list(limit: int = 150, admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 500)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT a.*,COALESCE(u.name,'System') user_name FROM audit_logs a
            LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT ?
            """, (limit,)
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json"))
        except json.JSONDecodeError:
            item["metadata"] = {}
        output.append(item)
    return output


@app.get("/api/admin/security")
def security_status(request: Request, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with connect() as conn:
        sessions = rows_to_dicts(conn.execute(
            """SELECT s.id,s.created_at,s.last_seen_at,s.expires_at,s.ip_address,s.user_agent,
                      u.name AS user_name,u.username
               FROM sessions s JOIN users u ON u.id=s.user_id
               ORDER BY s.last_seen_at DESC LIMIT 100"""
        ).fetchall())
        failed = conn.execute(
            "SELECT COUNT(*) c FROM audit_logs WHERE event_type='auth' AND action='login_failed' AND created_at >= datetime('now','-1 day')"
        ).fetchone()["c"]
        feedback_down = conn.execute(
            "SELECT COUNT(*) c FROM message_feedback WHERE rating='down' AND created_at >= datetime('now','-7 day')"
        ).fetchone()["c"]
        api_tokens = rows_to_dicts(conn.execute(
            """SELECT t.id,t.device_name,t.created_at,t.last_used_at,t.expires_at,
                      u.name AS user_name,u.username
               FROM api_tokens t JOIN users u ON u.id=t.user_id
               WHERE t.revoked=0 AND t.expires_at >= ?
               ORDER BY t.last_used_at DESC LIMIT 100""",
            (now_iso(),),
        ).fetchall())
    checks = [
        {"id":"secret","label":"Session secret","ok":settings.secret_is_strong,"detail":"48+ тэмдэгт random secret" if settings.secret_is_strong else "DUREM_SECRET_KEY-гээ урт random утгаар солино уу."},
        {"id":"cookies","label":"Secure cookies","ok":settings.secure_cookies,"detail":"HTTPS cookie enabled" if settings.secure_cookies else "Local HTTP mode. LAN production дээр HTTPS + DUREM_SECURE_COOKIES=true."},
        {"id":"hosts","label":"Trusted hosts","ok":"*" not in settings.allowed_hosts,"detail":", ".join(settings.allowed_hosts)},
        {"id":"passwords","label":"Password hashing","ok":True,"detail":"Argon2id (64 MiB, time cost 3) + legacy hash auto-upgrade."},
        {"id":"csrf","label":"CSRF protection","ok":True,"detail":"State-changing API бүр X-CSRF-Token шаарддаг."},
        {"id":"throttle","label":"Login throttling","ok":settings.login_attempts_per_10m > 0 and settings.login_ip_attempts_per_10m > 0,"detail":f"{settings.login_attempts_per_10m}/user+IP, {settings.login_ip_attempts_per_10m}/IP per 10 minutes."},
        {"id":"uploads","label":"Upload hardening","ok":True,"detail":"Type/signature/zip-bomb/macro checks enabled."},
        {"id":"docs","label":"API docs exposure","ok":not settings.enable_docs,"detail":"Disabled in production" if not settings.enable_docs else "DUREM_ENABLE_DOCS=true — production дээр унтраана уу."},
        {"id":"backup","label":"Encrypted backups","ok":True,"detail":"AES-256-GCM + PBKDF2 passphrase хамгаалалттай restore flow."},
        {"id":"local","label":"Local AI boundary","ok":settings.llm_endpoint_is_local and not settings.allow_external_ai,"detail":settings.llm_base_url if settings.llm_endpoint_is_local else "External AI endpoint configured — review data boundary."},
        {"id":"chatprivacy","label":"General chat audit privacy","ok":get_setting("store_raw_chat_questions", "0") != "1","detail":"Raw general-chat prompts audit log-д хадгалахгүй." if get_setting("store_raw_chat_questions", "0") != "1" else "Raw general-chat prompts audit log-д хадгалагдаж байна — privacy policy-гоо шалгана уу."},
        {"id":"router","label":"Hybrid safety router","ok":get_setting("hybrid_router_enabled", "1") == "1","detail":"Ambiguous prompt local classifier + conservative fallback." if get_setting("hybrid_router_enabled", "1") == "1" else "Deterministic-only routing enabled."},
        {"id":"api","label":"App API bearer tokens","ok":True,"detail":"Raw API token DB-д хадгалахгүй; SHA-256 hash + expiry/revoke ашиглана."},
    ]
    score = round(sum(1 for item in checks if item["ok"]) / len(checks) * 100)
    current = current_session_id(request) or ""
    for item in sessions:
        item["current"] = item["id"] == current
        item["id_short"] = item["id"][:10]
        item["user_agent"] = (item.get("user_agent") or "")[:180]
    return {"score": score, "checks": checks, "sessions": sessions, "api_tokens": api_tokens, "failed_logins_24h": failed, "negative_feedback_7d": feedback_down}


@app.delete("/api/admin/sessions/{session_id}")
def revoke_session(session_id: str, request: Request, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    audit(admin["id"], "security", "session_revoked", {"session_id": session_id[:12]})
    return {"deleted": cursor.rowcount > 0, "current": session_id == (current_session_id(request) or "")}


@app.delete("/api/admin/api-tokens/{token_id}")
def revoke_admin_api_token(token_id: str, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, bool]:
    with connect() as conn:
        cursor = conn.execute("UPDATE api_tokens SET revoked=1 WHERE id=? AND revoked=0", (token_id,))
    audit(admin["id"], "security", "api_token_revoked", {"token_id": token_id[:18]})
    return {"revoked": cursor.rowcount > 0}


@app.get("/api/admin/unanswered")
def unanswered(limit: int = 50, admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 200)
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.id,a.user_id,a.metadata_json,a.created_at,COALESCE(u.name,'Unknown') user_name
               FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
               WHERE a.event_type='assistant' AND a.action='answer'
               ORDER BY a.id DESC LIMIT 1000"""
        ).fetchall()
    output = []
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if meta.get("route", "policy") == "policy" and (meta.get("answer_type") == "NOT_FOUND" or meta.get("decision") == "NOT_FOUND"):
            output.append({"id": row["id"], "user_name": row["user_name"], "question": meta.get("question", ""), "mode": meta.get("mode", ""), "created_at": row["created_at"]})
            if len(output) >= limit:
                break
    return output


@app.get("/api/admin/settings")
def settings_get(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {
        "company_name": get_setting("company_name", settings.company_name),
        "llm_model": get_setting("llm_model", settings.llm_model),
        "embedding_model": get_setting("embedding_model", settings.embedding_model),
        "embeddings_enabled": get_setting("embeddings_enabled", "1") == "1",
        "general_chat_enabled": get_setting("general_chat_enabled", "1") == "1",
        "auto_routing_enabled": get_setting("auto_routing_enabled", "1") == "1",
        "hybrid_router_enabled": get_setting("hybrid_router_enabled", "1") == "1",
        "personal_memory_enabled": get_setting("personal_memory_enabled", "1") == "1",
        "api_access_enabled": get_setting("api_access_enabled", "1") == "1",
        "api_token_ttl_days": int(get_setting("api_token_ttl_days", "30")),
        "chat_history_messages": int(get_setting("chat_history_messages", "16")),
        "store_raw_chat_questions": get_setting("store_raw_chat_questions", "0") == "1",
        "lemonade_base_url": settings.llm_base_url,
        "data_dir": str(settings.data_dir),
    }


@app.put("/api/admin/settings")
def settings_save(payload: SettingInput, admin: dict[str, Any] = Depends(require_admin_csrf)) -> dict[str, Any]:
    set_setting("company_name", payload.company_name.strip())
    set_setting("llm_model", payload.llm_model.strip())
    set_setting("embedding_model", payload.embedding_model.strip())
    set_setting("embeddings_enabled", "1" if payload.embeddings_enabled else "0")
    set_setting("general_chat_enabled", "1" if payload.general_chat_enabled else "0")
    set_setting("auto_routing_enabled", "1" if payload.auto_routing_enabled else "0")
    set_setting("hybrid_router_enabled", "1" if payload.hybrid_router_enabled else "0")
    set_setting("personal_memory_enabled", "1" if payload.personal_memory_enabled else "0")
    set_setting("chat_history_messages", str(payload.chat_history_messages))
    set_setting("store_raw_chat_questions", "1" if payload.store_raw_chat_questions else "0")
    set_setting("api_access_enabled", "1" if payload.api_access_enabled else "0")
    set_setting("api_token_ttl_days", str(payload.api_token_ttl_days))
    if not payload.api_access_enabled:
        with connect() as conn:
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE revoked=0")
    audit(admin["id"], "admin", "settings_updated", {
        "company_name": payload.company_name.strip(), "llm_model": payload.llm_model.strip(),
        "embedding_model": payload.embedding_model.strip(), "embeddings_enabled": payload.embeddings_enabled,
        "general_chat_enabled": payload.general_chat_enabled, "auto_routing_enabled": payload.auto_routing_enabled,
        "hybrid_router_enabled": payload.hybrid_router_enabled, "personal_memory_enabled": payload.personal_memory_enabled,
        "chat_history_messages": payload.chat_history_messages, "store_raw_chat_questions": payload.store_raw_chat_questions,
        "api_access_enabled": payload.api_access_enabled, "api_token_ttl_days": payload.api_token_ttl_days,
    })
    return payload.model_dump()


@app.post("/api/admin/backup")
def backup(payload: BackupInput, admin: dict[str, Any] = Depends(require_admin_csrf)):
    try:
        path = create_backup(payload.passphrase)
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(admin["id"], "security", "encrypted_backup_created", {"file": path.name})
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.post("/api/admin/restore")
async def restore(
    file: UploadFile = File(...),
    passphrase: str = Form(""),
    admin: dict[str, Any] = Depends(require_admin_csrf),
) -> dict[str, Any]:
    suffix = Path(file.filename or "backup").suffix.lower()
    if suffix not in {".durem", ".zip"}:
        raise HTTPException(status_code=400, detail=".durem эсвэл .zip backup файл сонгоно уу.")
    max_bytes = settings.restore_max_mb * 1024 * 1024
    temp_path: Path | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="durem-upload-", suffix=suffix, dir=settings.data_dir, delete=False) as handle:
            temp_path = Path(handle.name)
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise BackupError(f"Backup файл {settings.restore_max_mb}MB-аас их байна.")
                handle.write(block)
        result = restore_backup(temp_path, passphrase)
        # Restore clears all sessions. Use a system audit entry in the restored database.
        audit(None, "security", "backup_restored", {
            "requested_by": admin.get("username", "admin"),
            "filename": Path(file.filename or "backup").name,
            "backup_version": result.get("backup_version", ""),
        })
        return result
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
