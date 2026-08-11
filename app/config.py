from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "DUREM AI"
    version: str = "2.2.0-rc1"
    company_name: str = os.getenv("DUREM_COMPANY_NAME", "Сутайн Буянт")
    host: str = os.getenv("DUREM_HOST", "127.0.0.1")
    port: int = _int("DUREM_PORT", 8080)
    data_dir: Path = Path(os.getenv("DUREM_DATA_DIR", str(ROOT / "data"))).resolve()

    # Security
    secret_key: str = os.getenv("DUREM_SECRET_KEY", "change-this-secret-before-lan-use")
    session_hours: int = _int("DUREM_SESSION_HOURS", 12)
    session_idle_minutes: int = _int("DUREM_SESSION_IDLE_MINUTES", 120)
    max_sessions_per_user: int = _int("DUREM_MAX_SESSIONS_PER_USER", 8)
    secure_cookies: bool = _bool("DUREM_SECURE_COOKIES", False)
    enable_docs: bool = _bool("DUREM_ENABLE_DOCS", False)
    allowed_hosts_raw: str = os.getenv("DUREM_ALLOWED_HOSTS", "*")
    rate_limit_per_minute: int = _int("DUREM_RATE_LIMIT_PER_MINUTE", 20)
    login_attempts_per_10m: int = _int("DUREM_LOGIN_ATTEMPTS_PER_10M", 10)
    login_ip_attempts_per_10m: int = _int("DUREM_LOGIN_IP_ATTEMPTS_PER_10M", 30)
    password_min_length: int = _int("DUREM_PASSWORD_MIN_LENGTH", 12)
    cors_origins_raw: str = os.getenv("DUREM_CORS_ORIGINS", "")

    # Documents
    upload_max_mb: int = _int("DUREM_UPLOAD_MAX_MB", 40)
    upload_max_uncompressed_mb: int = _int("DUREM_UPLOAD_MAX_UNCOMPRESSED_MB", 250)
    restore_max_mb: int = _int("DUREM_RESTORE_MAX_MB", 2048)

    # Local AI
    llm_base_url: str = os.getenv("LEMONADE_BASE_URL", "http://127.0.0.1:13305").rstrip("/")
    llm_api_key: str = os.getenv("LEMONADE_API_KEY", "").strip()
    allow_external_ai: bool = _bool("DUREM_ALLOW_EXTERNAL_AI", False)
    llm_model: str = os.getenv("LEMONADE_MODEL", "Qwen3-8B-GGUF")
    embedding_model: str = os.getenv("LEMONADE_EMBEDDING_MODEL", "Qwen3-Embedding-0.6B-GGUF")
    embeddings_enabled: bool = _bool("DUREM_EMBEDDINGS_ENABLED", True)
    llm_timeout_seconds: float = _float("LEMONADE_TIMEOUT_SECONDS", 240.0)
    llm_max_tokens: int = _int("DUREM_LLM_MAX_TOKENS", 900)
    disable_model_thinking: bool = _bool("DUREM_DISABLE_MODEL_THINKING", True)
    mock_mode: bool = _bool("DUREM_MOCK_MODE", False)
    demo_data: bool = _bool("DUREM_DEMO_DATA", False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "durem.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def allowed_hosts(self) -> list[str]:
        values = [item.strip() for item in self.allowed_hosts_raw.split(",") if item.strip()]
        return values or ["*"]


    @property
    def cors_origins(self) -> list[str]:
        values = [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]
        return [item for item in values if item != "*"]


    @property
    def llm_endpoint_is_local(self) -> bool:
        """Allow loopback, Docker host bridge, or literal private/link-local IP endpoints by default."""
        try:
            parsed = urlparse(self.llm_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            host = parsed.hostname.strip().lower()
            if host in {"localhost", "host.docker.internal"}:
                return True
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                # Hostnames are intentionally not DNS-resolved here: resolution can change
                # after startup and could silently turn a local-only product into egress.
                return False
            return bool(ip.is_loopback or ip.is_private or ip.is_link_local)
        except ValueError:
            return False

    @property
    def network_exposed(self) -> bool:
        return self.host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}

    @property
    def secret_is_strong(self) -> bool:
        weak = {
            "change-this-secret-before-lan-use",
            "replace-with-a-long-random-secret",
            "secret",
            "changeme",
        }
        return len(self.secret_key) >= 48 and self.secret_key.lower() not in weak


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.documents_dir.mkdir(parents=True, exist_ok=True)
settings.backups_dir.mkdir(parents=True, exist_ok=True)
