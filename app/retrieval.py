from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import settings
from .db import connect, get_setting
from .documents import cosine, lexical_score
from .lemonade import LemonadeClient, LemonadeError


@dataclass
class RetrievedRule:
    id: str
    title: str
    text: str
    category: str
    decision_hint: str
    approver: str
    source_section: str
    source_document_id: str
    metric: str
    min_value: float | None
    max_value: float | None
    min_inclusive: bool
    max_inclusive: bool
    priority: int
    score: float


@dataclass
class RetrievedChunk:
    id: str
    document_id: str
    document_title: str
    section: str
    content: str
    score: float


@dataclass
class RetrievedResponsibility:
    id: str
    topic: str
    target: str
    instructions: str
    score: float


def _scope_matches(scope_csv: str, actual: str) -> bool:
    values = [v.strip().lower() for v in (scope_csv or "").split(",") if v.strip()]
    return not values or actual.strip().lower() in values


def retrieve_rules(question: str, user: dict[str, Any], limit: int = 8) -> list[RetrievedRule]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM rules r
            LEFT JOIN documents d ON d.id=r.source_document_id
            WHERE r.active=1
              AND (r.source_document_id IS NULL OR r.source_document_id='' OR (
                   d.id IS NOT NULL AND d.status='active'
                   AND (d.effective_from='' OR d.effective_from <= date('now'))
                   AND (d.effective_to='' OR d.effective_to >= date('now'))
              ))
            ORDER BY r.priority DESC, r.updated_at DESC
            """
        ).fetchall()
    scored: list[RetrievedRule] = []
    for row in rows:
        if not _scope_matches(row["role_scope"], user.get("role", "")):
            continue
        if not _scope_matches(row["department_scope"], user.get("department", "")):
            continue
        combined = f"{row['title']} {row['text']} {row['keywords']} {row['category']}"
        score = lexical_score(question, combined)
        if score <= 0:
            continue
        score += min(0.15, row["priority"] / 10000)
        scored.append(
            RetrievedRule(
                id=row["id"], title=row["title"], text=row["text"], category=row["category"],
                decision_hint=row["decision_hint"], approver=row["approver"],
                source_section=row["source_section"], source_document_id=row["source_document_id"] or "",
                metric=row["metric"] or "", min_value=row["min_value"], max_value=row["max_value"],
                min_inclusive=bool(row["min_inclusive"]), max_inclusive=bool(row["max_inclusive"]), priority=int(row["priority"]), score=score,
            )
        )
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


def _document_access_clause(user: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if user.get("is_admin"):
        return "1=1", ()
    return "(d.visibility='all' OR (d.visibility='department' AND dep.name=?))", (user.get("department", ""),)


async def retrieve_chunks(question: str, user: dict[str, Any], limit: int = 6) -> list[RetrievedChunk]:
    clause, args = _document_access_clause(user)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id,c.document_id,c.section,c.content,c.embedding_json,d.title AS document_title
            FROM document_chunks c
            JOIN documents d ON d.id=c.document_id
            LEFT JOIN departments dep ON dep.id=d.department_id
            WHERE d.status='active' AND (d.effective_from='' OR d.effective_from <= date('now')) AND (d.effective_to='' OR d.effective_to >= date('now')) AND {clause}
            """,
            args,
        ).fetchall()

    query_vector: list[float] | None = None
    embeddings_on = get_setting("embeddings_enabled", "1") == "1"
    if embeddings_on and rows and not settings.mock_mode:
        try:
            vectors = await LemonadeClient().embeddings([question])
            query_vector = vectors[0] if vectors else None
        except LemonadeError:
            query_vector = None

    scored: list[RetrievedChunk] = []
    for row in rows:
        lexical = lexical_score(question, f"{row['document_title']} {row['section']} {row['content']}")
        semantic = 0.0
        if query_vector and row["embedding_json"]:
            try:
                semantic = max(0.0, cosine(query_vector, json.loads(row["embedding_json"])))
            except (ValueError, TypeError, json.JSONDecodeError):
                semantic = 0.0
        score = (0.48 * lexical + 0.52 * semantic) if semantic else lexical
        if score > 0.04:
            scored.append(
                RetrievedChunk(
                    id=row["id"], document_id=row["document_id"], document_title=row["document_title"],
                    section=row["section"], content=row["content"], score=score,
                )
            )
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


def retrieve_responsibilities(question: str, limit: int = 4) -> list[RetrievedResponsibility]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, COALESCE(d.name,'') department_name,
                   COALESCE(u.name,'') user_name, COALESCE(ro.name,'') role_name
            FROM responsibilities r
            LEFT JOIN departments d ON d.id=r.department_id
            LEFT JOIN users u ON u.id=r.user_id
            LEFT JOIN roles ro ON ro.id=r.role_id
            WHERE r.active=1
            """
        ).fetchall()
    results = []
    for row in rows:
        score = lexical_score(question, f"{row['topic']} {row['keywords']} {row['instructions']}")
        if score <= 0:
            continue
        target_parts = [part for part in (row["user_name"], row["role_name"], row["department_name"]) if part]
        target = " · ".join(target_parts) or "Хариуцсан ажилтан"
        results.append(
            RetrievedResponsibility(
                id=row["id"], topic=row["topic"], target=target,
                instructions=row["instructions"], score=score,
            )
        )
    return sorted(results, key=lambda item: item.score, reverse=True)[:limit]
