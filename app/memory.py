from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .db import connect, get_setting, now_iso


@dataclass(frozen=True)
class MemoryCommandResult:
    answer: str
    action: str
    changed: bool = False


_SENSITIVE = re.compile(
    r"(?:password|passcode|нууц\s*үг|api[_ -]?key|access[_ -]?key|secret|token|otp|pin|cvv|private\s*key|"
    r"seed\s*phrase|mnemonic|card\s*number|картын\s*дугаар|регистр(?:ийн)?\s*дугаар|паспорт)",
    flags=re.IGNORECASE,
)

_POLICY_AUTHORITY = re.compile(
    r"(?:компан(?:и|ийн)|байгууллаг(?:а|ын)|дүрэм|журам|policy|procedure|approval|зөвшөөр(?:өл|сөн|дөг)|"
    r"эрхтэй|эрхгүй|болохгүй|хориг|хөнгөлөлт|discount|менежер|захирал|гэрээ|nda)",
    flags=re.IGNORECASE,
)


def memory_enabled() -> bool:
    return get_setting("personal_memory_enabled", "1") == "1"


def list_memories(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id,key,value,category,source,created_at,updated_at
               FROM user_memories WHERE user_id=? AND active=1
               ORDER BY updated_at DESC LIMIT ?""",
            (user_id, max(1, min(limit, 50))),
        ).fetchall()
    return [dict(row) for row in rows]


def remember(user_id: int, key: str, value: str, category: str = "preference", source: str = "explicit") -> bool:
    key = key.strip()[:80]
    value = value.strip()[:600]
    if not key or not value:
        return False
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO user_memories(user_id,key,value,category,source,active,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)
               ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,category=excluded.category,
                 source=excluded.source,active=1,updated_at=excluded.updated_at""",
            (user_id, key, value, category[:40], source[:40], stamp, stamp),
        )
    return True


def forget_all(user_id: int) -> int:
    with connect() as conn:
        cursor = conn.execute("UPDATE user_memories SET active=0,updated_at=? WHERE user_id=? AND active=1", (now_iso(), user_id))
    return int(cursor.rowcount)


def forget_matching(user_id: int, needle: str) -> int:
    term = needle.strip()[:120]
    if not term:
        return 0
    like = f"%{term}%"
    with connect() as conn:
        cursor = conn.execute(
            """UPDATE user_memories SET active=0,updated_at=?
               WHERE user_id=? AND active=1 AND (key LIKE ? COLLATE NOCASE OR value LIKE ? COLLATE NOCASE)""",
            (now_iso(), user_id, like, like),
        )
    return int(cursor.rowcount)


def memory_prompt(user_id: int) -> str:
    if not memory_enabled():
        return "(personal memory disabled)"
    items = list_memories(user_id, limit=16)
    if not items:
        return "(none)"
    lines = [f"- {item['key']}: {item['value']}" for item in reversed(items)]
    return "\n".join(lines)[:2400]


def _safe_memory_value(value: str) -> tuple[bool, str]:
    clean = value.strip()
    if len(clean) < 2:
        return False, "Хадгалах утга хэт богино байна."
    if _SENSITIVE.search(clean):
        return False, "Нууц үг, token, API key, OTP зэрэг эмзэг credential-ийг personal memory-д хадгалахгүй."
    if _POLICY_AUTHORITY.search(clean):
        return False, "Компанийн дүрэм, зөвшөөрөл, эрхийн мэдээллийг personal memory болгохгүй. Тэр мэдээлэл зөвхөн батлагдсан дүрэм/баримтаас ирнэ."
    return True, ""


def _note_key(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"note_{digest}"


def handle_memory_command(question: str, user_id: int) -> MemoryCommandResult | None:
    text = question.strip()
    lower = text.lower()
    is_memory_intent = any(x in lower for x in (
        "санаж ав", "санаад ав", "remember this", "remember that", "юу санаж", "what do you remember",
        "бүгдийг март", "memory-г арилга", "memory г арилга", "forget everything", "forget all", "-г март", "ийг март",
    ))
    if not is_memory_intent:
        return None
    if not memory_enabled():
        return MemoryCommandResult("Personal memory одоогоор админаар унтраалттай байна.", "memory_disabled")

    if any(x in lower for x in ("юу санаж", "what do you remember")):
        items = list_memories(user_id)
        if not items:
            return MemoryCommandResult("Одоогоор таны талаар хадгалсан personal memory алга.", "memory_list")
        body = "\n".join(f"• {item['value']}" for item in items)
        return MemoryCommandResult(f"Таны personal memory-д одоогоор:\n{body}\n\nЭдгээр нь зөвхөн таны чатад ашиглагдана; компанийн дүрэм гэж тооцогдохгүй.", "memory_list")

    if any(x in lower for x in ("бүгдийг март", "memory-г арилга", "memory г арилга", "forget everything", "forget all")):
        count = forget_all(user_id)
        return MemoryCommandResult(f"Personal memory-гээ цэвэрлэлээ. {count} зүйл мартсан.", "memory_clear", changed=count > 0)

    forget_match = re.search(r"(?:^|\s)(.+?)(?:-г|ийг|ыг|г)\s+март(?:аарай)?[.!?]*$", text, flags=re.IGNORECASE)
    if forget_match:
        needle = forget_match.group(1).strip(" :'\"")
        count = forget_matching(user_id, needle)
        answer = f"“{needle}” гэсэн memory-г мартлаа." if count else f"“{needle}” гэсэн хадгалсан memory олдсонгүй."
        return MemoryCommandResult(answer, "memory_forget", changed=count > 0)

    remember_match = re.search(
        r"(?:санаж\s+ав(?:аарай)?|санаад\s+ав|remember\s+(?:this|that))\s*[:\-]?\s*(.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if remember_match:
        value = remember_match.group(1).strip()
        ok, reason = _safe_memory_value(value)
        if not ok:
            return MemoryCommandResult(reason, "memory_rejected")
        saved = remember(user_id, _note_key(value), value, category="note", source="explicit")
        return MemoryCommandResult("Санаж авлаа. Энэ нь зөвхөн таны personal memory-д хадгалагдана.", "memory_remember", changed=saved)

    return None


def learn_explicit_preferences(question: str, user_id: int) -> list[str]:
    if not memory_enabled():
        return []
    text = question.strip()
    lower = text.lower()
    learned: list[str] = []

    name_match = re.search(r"намайг\s+([A-Za-zА-Яа-яӨөҮүЁё0-9_.-]{1,40})\s+(?:гэж\s+)?дууд(?:аарай)?", text, flags=re.IGNORECASE)
    if not name_match:
        name_match = re.search(r"\bcall\s+me\s+([A-Za-z0-9_.-]{1,40})\b", text, flags=re.IGNORECASE)
    if name_match and remember(user_id, "preferred_name", name_match.group(1), "preference", "explicit"):
        learned.append("preferred_name")

    if any(x in lower for x in ("товч хариул", "богино хариул", "concise answers", "keep it concise")):
        if remember(user_id, "response_style", "Товч, шууд хариулт илүүд үздэг.", "preference", "explicit"):
            learned.append("response_style")
    elif any(x in lower for x in ("дэлгэрэнгүй хариул", "нарийн тайлбарла", "detailed answers", "explain in detail")):
        if remember(user_id, "response_style", "Дэлгэрэнгүй, тайлбартай хариулт илүүд үздэг.", "preference", "explicit"):
            learned.append("response_style")

    if any(x in lower for x in ("монголоор хариул", "монгол хэлээр хариул")):
        if remember(user_id, "language", "Монгол хэлээр хариулах.", "preference", "explicit"):
            learned.append("language")
    elif any(x in lower for x in ("англиар хариул", "englishээр хариул", "answer in english")):
        if remember(user_id, "language", "Answer in English.", "preference", "explicit"):
            learned.append("language")

    if any(x in lower for x in ("албан ёсны өнгө", "formal tone")):
        if remember(user_id, "tone", "Албан ёсны, мэргэжлийн өнгө аястай.", "preference", "explicit"):
            learned.append("tone")
    elif any(x in lower for x in ("энгийн өнгө", "casual tone", "friendly tone")):
        if remember(user_id, "tone", "Энгийн, найрсаг өнгө аястай.", "preference", "explicit"):
            learned.append("tone")

    return learned
