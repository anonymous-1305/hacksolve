"""Configuration. Everything comes from environment variables; no secrets live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


def load_env_file(path: Path) -> None:
    """Tiny .env reader (KEY=VALUE). Real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))
    web_dir: Path = field(default_factory=lambda: ROOT / "web")
    max_upload_bytes: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 5) * 1024 * 1024)
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 14))
    cookie_secure: bool = field(default_factory=lambda: _flag("COOKIE_SECURE"))
    trust_proxy: bool = field(default_factory=lambda: _flag("TRUST_PROXY"))
    allowed_origins: tuple = field(default_factory=lambda: tuple(
        o.strip().rstrip("/") for o in _env("ALLOWED_ORIGINS").split(",") if o.strip()))
    ai_provider: str = field(default_factory=lambda: _env("AI_PROVIDER", "auto").lower())
    models: dict = field(default_factory=lambda: {
        "anthropic": _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "openai": _env("OPENAI_MODEL", "gpt-4o-mini"),
        "gemini": _env("GEMINI_MODEL", "gemini-2.0-flash"),
    })
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    keys: dict = field(default_factory=lambda: {n: _env(n) for n in SECRET_NAMES}, repr=False)
    rate_auth: int = field(default_factory=lambda: _int("RATE_AUTH_PER_MIN", 10))
    rate_analyze: int = field(default_factory=lambda: _int("RATE_ANALYZE_PER_MIN", 6))
    rate_upload: int = field(default_factory=lambda: _int("RATE_UPLOAD_PER_MIN", 20))
    workers: int = field(default_factory=lambda: _int("WORKERS", 2))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hacksolve.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"
