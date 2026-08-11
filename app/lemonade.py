from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings
from .db import get_setting


class LemonadeError(RuntimeError):
    pass


@dataclass
class LemonadeResult:
    content: str
    endpoint: str


class LemonadeClient:
    def __init__(self) -> None:
        self.base_url = settings.llm_base_url
        self.timeout = settings.llm_timeout_seconds
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}

    @property
    def llm_model(self) -> str:
        return get_setting("llm_model", settings.llm_model)

    @property
    def embedding_model(self) -> str:
        return get_setting("embedding_model", settings.embedding_model)

    async def health(self) -> tuple[bool, list[str]]:
        try:
            async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/v1/models", headers=self.headers)
            if not response.is_success:
                return False, []
            data = response.json().get("data", [])
            return True, [str(item.get("id", "")) for item in data]
        except (httpx.HTTPError, ValueError, TypeError):
            return False, []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.55,
        max_tokens: int | None = None,
    ) -> LemonadeResult:
        clean_messages = [
            {"role": str(item.get("role", "user")), "content": str(item.get("content", ""))}
            for item in messages
            if str(item.get("content", "")).strip()
        ]
        if not clean_messages:
            raise LemonadeError("Local AI-д илгээх message хоосон байна.")
        if settings.disable_model_thinking and "qwen3" in self.llm_model.lower():
            clean_messages = [dict(item) for item in clean_messages]
            for index in range(len(clean_messages) - 1, -1, -1):
                if clean_messages[index]["role"] == "user":
                    clean_messages[index]["content"] = clean_messages[index]["content"].rstrip() + "\n\n/no_think"
                    break
        payload = {
            "model": self.llm_model,
            "messages": clean_messages,
            "stream": False,
            "temperature": max(0.0, min(float(temperature), 1.5)),
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/v1/chat/completions", json=payload, headers=self.headers)
        if not response.is_success:
            raise LemonadeError(f"Local AI runtime HTTP {response.status_code} алдаа өглөө.")
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LemonadeError("Local AI runtime хариуны бүтэц буруу байна.") from exc
        return LemonadeResult(content=content.strip(), endpoint="/v1/chat/completions")

    async def chat_json(self, system_prompt: str, user_prompt: str) -> LemonadeResult:
        return await self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )

    async def embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.embedding_model,
            "input": texts,
            "encoding_format": "float",
        }
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/v1/embeddings", json=payload, headers=self.headers)
        if not response.is_success:
            raise LemonadeError(f"Local embedding runtime HTTP {response.status_code} алдаа өглөө.")
        data = response.json().get("data", [])
        data = sorted(data, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in data]
        if len(vectors) != len(texts) or any(not isinstance(v, list) for v in vectors):
            raise LemonadeError("Embedding response shape is invalid")
        return vectors


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start < 0:
        raise ValueError("No JSON object found")
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(cleaned[start:idx + 1])
                if not isinstance(value, dict):
                    raise ValueError("JSON is not an object")
                return value
    raise ValueError("Incomplete JSON object")
