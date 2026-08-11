from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows_to_dicts(rows: list[sqlite3.Row] | sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                device_name TEXT NOT NULL DEFAULT 'DUREM App',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT '',
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id, revoked, expires_at);

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT 'general',
                visibility TEXT NOT NULL DEFAULT 'all',
                department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                version TEXT NOT NULL DEFAULT '1.0',
                status TEXT NOT NULL DEFAULT 'active',
                effective_from TEXT NOT NULL DEFAULT '',
                effective_to TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                index_mode TEXT NOT NULL DEFAULT 'lexical',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);

            CREATE TABLE IF NOT EXISTS document_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                embedding_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);

            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                keywords TEXT NOT NULL DEFAULT '',
                decision_hint TEXT NOT NULL DEFAULT 'AUTO',
                approver TEXT NOT NULL DEFAULT '',
                role_scope TEXT NOT NULL DEFAULT '',
                department_scope TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 100,
                metric TEXT NOT NULL DEFAULT '',
                min_value REAL,
                max_value REAL,
                min_inclusive INTEGER NOT NULL DEFAULT 1,
                max_inclusive INTEGER NOT NULL DEFAULT 1,
                source_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                source_section TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS responsibilities (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                keywords TEXT NOT NULL,
                department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                role_id INTEGER REFERENCES roles(id) ON DELETE SET NULL,
                instructions TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT 'Шинэ яриа',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

            CREATE TABLE IF NOT EXISTS user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'preference',
                source TEXT NOT NULL DEFAULT 'explicit',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, key)
            );
            CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id, active, updated_at DESC);

            CREATE TABLE IF NOT EXISTS message_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                assistant_message_id TEXT,
                rating TEXT NOT NULL CHECK(rating IN ('up','down')),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_created ON message_feedback(created_at DESC);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                action TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_type_action ON audit_logs(event_type, action, created_at DESC);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _ensure_rule_columns(conn)
        _ensure_session_columns(conn)
        _seed(conn)


def _ensure_rule_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(rules)").fetchall()}
    additions = {
        "metric": "TEXT NOT NULL DEFAULT ''",
        "min_value": "REAL",
        "max_value": "REAL",
        "min_inclusive": "INTEGER NOT NULL DEFAULT 1",
        "max_inclusive": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE rules ADD COLUMN {name} {definition}")


def _ensure_session_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    additions = {
        "last_seen_at": "TEXT NOT NULL DEFAULT ''",
        "ip_address": "TEXT NOT NULL DEFAULT ''",
        "user_agent": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    # Migrate old rows so idle-session logic has a sane timestamp.
    conn.execute("UPDATE sessions SET last_seen_at=created_at WHERE last_seen_at='' OR last_seen_at IS NULL")


def _seed(conn: sqlite3.Connection) -> None:
    created = now_iso()
    departments = [
        ("Ерөнхий", "Компанийн ерөнхий хэрэглэгчид"),
        ("Борлуулалт", "Борлуулалтын баг"),
        ("Санхүү", "Санхүү, тооцооны баг"),
        ("Хүний нөөц", "HR"),
        ("Хууль", "Гэрээ, хууль эрх зүй"),
        ("IT", "Мэдээллийн технологи"),
    ]
    for name, description in departments:
        conn.execute(
            "INSERT OR IGNORE INTO departments(name, description, active, created_at) VALUES(?,?,1,?)",
            (name, description, created),
        )

    roles = [
        ("Ажилтан", "Ердийн ажилтан", 0),
        ("Борлуулалтын ажилтан", "Борлуулалтын ажилтан", 0),
        ("Менежер", "Хэлтсийн менежер", 0),
        ("Админ", "DUREM системийн админ", 1),
    ]
    for name, description, is_admin in roles:
        conn.execute(
            "INSERT OR IGNORE INTO roles(name, description, is_admin, active, created_at) VALUES(?,?,?,?,?)",
            (name, description, is_admin, 1, created),
        )

    defaults = {
        "company_name": settings.company_name,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "embeddings_enabled": "1" if settings.embeddings_enabled else "0",
        "general_chat_enabled": "1",
        "auto_routing_enabled": "1",
        "hybrid_router_enabled": "1",
        "personal_memory_enabled": "1",
        "chat_history_messages": "16",
        "store_raw_chat_questions": "0",
        "api_access_enabled": "1",
        "api_token_ttl_days": "30",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
            (key, str(value), created),
        )

    sample_rules = [
        {
            "id": "vehicle-001", "title": "Компанийн автомашины үндсэн хэрэглээ",
            "text": "Компанийн автомашиныг зөвхөн албан ажлын зориулалтаар ашиглана.",
            "category": "operations", "keywords": "машин,автомашин,унаа", "decision_hint": "AUTO",
            "approver": "", "priority": 100, "metric": "", "min_value": None, "max_value": None,
            "min_inclusive": 1, "max_inclusive": 1,
        },
        {
            "id": "vehicle-002", "title": "Компанийн автомашины хувийн хэрэглээ",
            "text": "Компанийн автомашиныг хувийн хэрэгцээнд ашиглах бол захирлын бичгээр өгсөн зөвшөөрөл шаардлагатай.",
            "category": "operations", "keywords": "машин,автомашин,амралт,хувийн", "decision_hint": "APPROVAL_REQUIRED",
            "approver": "Захирал", "priority": 200, "metric": "", "min_value": None, "max_value": None,
            "min_inclusive": 1, "max_inclusive": 1,
        },
        {
            "id": "discount-001", "title": "5 хувь хүртэлх хөнгөлөлт",
            "text": "Борлуулалтын ажилтан 5 хувь хүртэлх хөнгөлөлтийг өөрөө өгөх эрхтэй.",
            "category": "sales", "keywords": "хөнгөлөлт,discount,5%", "decision_hint": "ALLOWED",
            "approver": "", "priority": 100, "metric": "percent", "min_value": 0, "max_value": 5,
            "min_inclusive": 1, "max_inclusive": 1,
        },
        {
            "id": "discount-002", "title": "5-10 хувийн хөнгөлөлт",
            "text": "5 хувиас дээш, 10 хувь хүртэлх хөнгөлөлтөд борлуулалтын менежерийн зөвшөөрөл авна.",
            "category": "sales", "keywords": "хөнгөлөлт,discount,8%,10%", "decision_hint": "APPROVAL_REQUIRED",
            "approver": "Борлуулалтын менежер", "priority": 200, "metric": "percent", "min_value": 5, "max_value": 10,
            "min_inclusive": 0, "max_inclusive": 1,
        },
        {
            "id": "discount-003", "title": "10 хувиас дээш хөнгөлөлт",
            "text": "10 хувиас дээш хөнгөлөлтийг зөвхөн ерөнхий захирал батална.",
            "category": "sales", "keywords": "хөнгөлөлт,discount,10%", "decision_hint": "APPROVAL_REQUIRED",
            "approver": "Ерөнхий захирал", "priority": 300, "metric": "percent", "min_value": 10, "max_value": None,
            "min_inclusive": 0, "max_inclusive": 1,
        },
    ]
    if not settings.demo_data:
        sample_rules = []

    for rule in sample_rules:
        conn.execute(
            """
            INSERT OR IGNORE INTO rules(
                id,title,text,category,keywords,decision_hint,approver,role_scope,department_scope,
                priority,metric,min_value,max_value,min_inclusive,max_inclusive,source_section,active,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,'','',?,?,?,?,?,?,'',1,?,?)
            """,
            (
                rule["id"], rule["title"], rule["text"], rule["category"], rule["keywords"],
                rule["decision_hint"], rule["approver"], rule["priority"], rule.get("metric", ""),
                rule.get("min_value"), rule.get("max_value"), rule.get("min_inclusive", 1), rule.get("max_inclusive", 1),
                created, created,
            ),
        )


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now_iso()),
        )


def audit(user_id: int | None, event_type: str, action: str, metadata: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs(user_id,event_type,action,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (user_id, event_type, action, json.dumps(metadata or {}, ensure_ascii=False), now_iso()),
        )


def db_ok() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def ensure_paths() -> None:
    for path in (settings.data_dir, settings.documents_dir, settings.backups_dir):
        Path(path).mkdir(parents=True, exist_ok=True)
