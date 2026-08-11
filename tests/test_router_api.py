from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.assistant_router import route_question_hybrid
from app.auth import ensure_default_admin, hash_password
from app.db import connect, init_db, now_iso, set_setting
from app.lemonade import LemonadeClient, LemonadeError, LemonadeResult
from app.main import app


API_PASSWORD = "Api-Test-Strong-123!"


def _ensure_api_user(username: str = "api_test_user", name: str = "API Test User") -> int:
    init_db()
    ensure_default_admin()
    stamp = now_iso()
    with connect() as conn:
        role = conn.execute("SELECT id FROM roles WHERE active=1 ORDER BY is_admin,id LIMIT 1").fetchone()
        dep = conn.execute("SELECT id FROM departments WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            user_id = int(row["id"])
            conn.execute(
                "UPDATE users SET name=?,password_hash=?,active=1,role_id=?,department_id=?,updated_at=? WHERE id=?",
                (name, hash_password(API_PASSWORD), role["id"] if role else None, dep["id"] if dep else None, stamp, user_id),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO users(username,name,password_hash,department_id,role_id,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,1,?,?)""",
                (username, name, hash_password(API_PASSWORD), dep["id"] if dep else None, role["id"] if role else None, stamp, stamp),
            )
            user_id = int(cursor.lastrowid)
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user_id,))
    return user_id


def _login(client: TestClient, username: str = "api_test_user") -> tuple[str, dict]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": API_PASSWORD, "device_name": "pytest app"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"].startswith("durem_v1_")
    return data["access_token"], data


@pytest.fixture(autouse=True)
def _api_settings():
    init_db()
    set_setting("api_access_enabled", "1")
    set_setting("api_token_ttl_days", "30")
    set_setting("auto_routing_enabled", "1")
    set_setting("hybrid_router_enabled", "1")
    set_setting("general_chat_enabled", "1")
    yield


@pytest.mark.asyncio
async def test_hybrid_router_calls_local_classifier_only_for_ambiguous(monkeypatch):
    calls = []

    async def fake_chat(self, messages, **kwargs):
        calls.append(messages)
        return LemonadeResult('{"route":"chat","confidence":0.91,"reason_code":"general_explanation"}', "/v1/chat/completions")

    monkeypatch.setattr(LemonadeClient, "chat", fake_chat)
    direct = await route_question_hybrid("Сайн уу, brainstorm хийе", "auto")
    assert direct.route == "chat"
    assert direct.classifier_invoked is False

    ambiguous = await route_question_hybrid("NDA дээр юуг анхаарах вэ?", "auto")
    assert ambiguous.route == "chat"
    assert ambiguous.classifier_invoked is True
    assert ambiguous.method == "llm_classifier"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_hybrid_router_policy_bias_on_low_confidence_chat(monkeypatch):
    async def fake_chat(self, messages, **kwargs):
        return LemonadeResult('{"route":"chat","confidence":0.41,"reason_code":"uncertain"}', "/v1/chat/completions")

    monkeypatch.setattr(LemonadeClient, "chat", fake_chat)
    decision = await route_question_hybrid("NDA дээр юуг анхаарах вэ?", "auto")
    assert decision.route == "policy"
    assert decision.method == "fallback"
    assert decision.classifier_invoked is True


@pytest.mark.asyncio
async def test_hybrid_router_classifier_failure_is_safe(monkeypatch):
    async def fail_chat(self, messages, **kwargs):
        raise LemonadeError("offline")

    monkeypatch.setattr(LemonadeClient, "chat", fail_chat)
    decision = await route_question_hybrid("NDA дээр юуг анхаарах вэ?", "auto")
    assert decision.route == "policy"
    assert decision.reason == "fallback:classifier_unavailable"


@pytest.mark.asyncio
async def test_manual_chat_cannot_bypass_company_policy(monkeypatch):
    decision = await route_question_hybrid("Манай компанид маргааш чөлөө авч болох уу?", "chat")
    assert decision.route == "policy"
    assert decision.safety_override is True
    assert decision.classifier_invoked is False


def test_api_bearer_login_hash_storage_and_revoke():
    user_id = _ensure_api_user()
    client = TestClient(app)
    token, data = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "api_test_user"

    with connect() as conn:
        row = conn.execute("SELECT token_hash FROM api_tokens WHERE user_id=? AND revoked=0", (user_id,)).fetchone()
    assert row
    assert row["token_hash"] != token
    assert token not in row["token_hash"]

    logout = client.delete("/api/v1/auth/session", headers=headers)
    assert logout.status_code == 200 and logout.json()["revoked"] is True
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_api_expired_token_is_rejected():
    user_id = _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    with connect() as conn:
        conn.execute(
            "UPDATE api_tokens SET expires_at=? WHERE user_id=? AND revoked=0",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"), user_id),
        )
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_api_route_endpoint_and_policy_safety_override():
    _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    chat = client.post(
        "/api/v1/assistant/route",
        headers=headers,
        json={"question": "Энэ email-ийг мэргэжлийн болгож өг", "mode": "auto"},
    )
    assert chat.status_code == 200
    assert chat.json()["route"] == "chat"

    policy = client.post(
        "/api/v1/assistant/route",
        headers=headers,
        json={"question": "Манай компанид 8% хөнгөлөлт өгч болох уу?", "mode": "chat"},
    )
    assert policy.status_code == 200
    data = policy.json()
    assert data["route"] == "policy"
    assert data["safety_override"] is True
    assert data["route_method"] == "deterministic"


def test_api_general_chat_and_policy_answer_contract(monkeypatch):
    _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    async def fake_chat(self, messages, **kwargs):
        # General-chat request below routes deterministically, so this is answer generation.
        return LemonadeResult("Сайн байна. Юун дээр хамт ажиллах вэ?", "/v1/chat/completions")

    monkeypatch.setattr(LemonadeClient, "chat", fake_chat)
    chat = client.post(
        "/api/v1/assistant/ask",
        headers=headers,
        json={"question": "Сайн уу, brainstorm хийе", "mode": "auto"},
    )
    assert chat.status_code == 200, chat.text
    chat_data = chat.json()
    assert chat_data["answer_type"] == "CHAT"
    assert chat_data["route"] == "chat"
    assert chat_data["sources"] == []
    assert "route_confidence" in chat_data
    assert "route_method" in chat_data

    # No approved knowledge is required to prove the route contract; policy path
    # must fail safe instead of returning general-chat content.
    policy = client.post(
        "/api/v1/assistant/ask",
        headers=headers,
        json={"question": "Манай компанийн чөлөөний дүрэм юу вэ?", "mode": "auto"},
    )
    assert policy.status_code == 200, policy.text
    policy_data = policy.json()
    assert policy_data["route"] == "policy"
    assert policy_data["answer_type"] == "NOT_FOUND"
    assert policy_data["sources"] == []


def test_api_conversation_isolation():
    user_a = _ensure_api_user("api_user_a", "API User A")
    user_b = _ensure_api_user("api_user_b", "API User B")
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO conversations(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("conv_private_a", user_a, "Private A", stamp, stamp),
        )
    client = TestClient(app)
    token_b, _ = _login(client, "api_user_b")
    response = client.get(
        "/api/v1/conversations/conv_private_a",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
    with connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id='conv_private_a'")
        conn.execute("DELETE FROM api_tokens WHERE user_id IN (?,?)", (user_a, user_b))


def test_api_session_listing_marks_current():
    _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    response = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    sessions = response.json()
    assert sessions and any(item["current"] for item in sessions)
    assert all("token_hash" not in item for item in sessions)


def test_api_password_change_revokes_all_sessions():
    _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    new_password = "Api-New-Strong-456!"
    changed = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": API_PASSWORD, "new_password": new_password},
    )
    assert changed.status_code == 200
    assert changed.json()["logout_required"] is True
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401

    old_login = client.post(
        "/api/v1/auth/login",
        json={"username": "api_test_user", "password": API_PASSWORD, "device_name": "old password"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/v1/auth/login",
        json={"username": "api_test_user", "password": new_password, "device_name": "new password"},
    )
    assert new_login.status_code == 200
    # Restore fixture password for later/manual runs.
    _ensure_api_user()


def test_api_document_preview_and_file_respect_lifecycle():
    user_id = _ensure_api_user()
    client = TestClient(app)
    token, _ = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    doc_id = "api-doc-test"
    stored = "api-doc-test.txt"
    from app.config import settings
    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    (settings.documents_dir / stored).write_text("Approved API document", encoding="utf-8")
    stamp = now_iso()
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.execute(
            """INSERT INTO documents(
                id,title,filename,stored_name,mime_type,size_bytes,category,visibility,department_id,version,status,
                effective_from,effective_to,checksum,index_mode,chunk_count,created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, "API Test Document", "api-test.txt", stored, "text/plain", 21, "test", "all", None, "1.0", "active",
             "", "", "test-checksum", "lexical", 1, user_id, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO document_chunks(id,document_id,chunk_index,section,content,embedding_json,created_at) VALUES(?,?,?,?,?,?,?)",
            ("api-doc-test-chunk", doc_id, 0, "Test", "Approved API document", None, stamp),
        )

    preview = client.get(f"/api/v1/documents/{doc_id}/preview", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["chunks"][0]["content"] == "Approved API document"
    file_response = client.get(f"/api/v1/documents/{doc_id}/file", headers=headers)
    assert file_response.status_code == 200
    assert file_response.content == b"Approved API document"

    with connect() as conn:
        conn.execute("UPDATE documents SET status='archived' WHERE id=?", (doc_id,))
    assert client.get(f"/api/v1/documents/{doc_id}/preview", headers=headers).status_code == 403
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    (settings.documents_dir / stored).unlink(missing_ok=True)
