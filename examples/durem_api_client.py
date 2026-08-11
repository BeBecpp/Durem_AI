"""Small first-party DUREM App API client example."""
from __future__ import annotations

from typing import Any

import httpx


class DuremClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = ""

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("Call login() first")
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method: str, path: str, *, json: Any | None = None, timeout: float = 300) -> Any:
        response = httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=json,
            timeout=timeout,
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def login(self, username: str, password: str, device_name: str = "DUREM Python Client") -> dict:
        response = httpx.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password, "device_name": device_name},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        return data

    def me(self) -> dict:
        return self._request("GET", "/api/v1/auth/me", timeout=30)

    def ask(self, question: str, mode: str = "auto", conversation_id: str | None = None) -> dict:
        return self._request(
            "POST",
            "/api/v1/assistant/ask",
            json={"question": question, "mode": mode, "conversation_id": conversation_id},
        )

    def route(self, question: str, mode: str = "auto", conversation_id: str | None = None) -> dict:
        return self._request(
            "POST",
            "/api/v1/assistant/route",
            json={"question": question, "mode": mode, "conversation_id": conversation_id},
        )

    def conversations(self) -> list[dict]:
        return self._request("GET", "/api/v1/conversations", timeout=30)

    def memory(self) -> dict:
        return self._request("GET", "/api/v1/memory", timeout=30)

    def source_preview(self, document_id: str) -> dict:
        return self._request("GET", f"/api/v1/documents/{document_id}/preview", timeout=30)

    def feedback(self, conversation_id: str, rating: str, note: str = "") -> dict:
        return self._request(
            "POST",
            "/api/v1/feedback",
            json={"conversation_id": conversation_id, "assistant_message_id": "", "rating": rating, "note": note},
            timeout=30,
        )

    def logout(self) -> None:
        if self.token:
            try:
                self._request("DELETE", "/api/v1/auth/session", timeout=30)
            finally:
                self.token = ""
