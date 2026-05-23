"""Centralized configuration via pydantic-settings.

Every env variable is documented in `.env.example`. All variables are optional —
the server runs zero-config out of the box.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BackendName = Literal["synthea", "hapi"]


def _default_state_dir() -> Path:
    return Path.home() / ".fhir-mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FHIR_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- FHIR backend -----------------------------------------------------
    backend: BackendName = "synthea"
    hapi_base_url: str = "https://hapi.fhir.org/baseR4"

    # --- De-id ------------------------------------------------------------
    enable_reid: bool = False
    reid_key: str | None = None

    # --- Storage ----------------------------------------------------------
    state_dir: Path = Field(default_factory=_default_state_dir)
    audit_url: str | None = None  # if unset → sqlite under state_dir/audit.db
    vault_url_override: str | None = Field(default=None, alias="FHIR_MCP_VAULT_URL")

    # --- Cloud opt-in (no defaults — must be explicitly set) --------------
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_service_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_KEY")
    supabase_db_url: str | None = Field(default=None, alias="SUPABASE_DB_URL")
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")

    # --- SMART on FHIR (v0.2+) -------------------------------------------
    smart_issuer: str | None = None
    smart_client_id: str | None = None
    smart_redirect_uri: str = "http://localhost:8765/smart/callback"
    smart_scopes: str = "patient/*.read fhir-mcp/reid"
    smart_private_key_pem: str | None = None  # backend-services flow
    smart_key_id: str | None = None

    # --- Eval / judge -----------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    eval_judge_model: str = "claude-sonnet-4-6"

    # --- External services ------------------------------------------------
    cds_hooks_url: str = "https://cds.logicahealth.org"
    terminology_url: str = "https://tx.fhir.org/r4"

    # --- Logging ----------------------------------------------------------
    log_level: str = "INFO"
    verbose_audit: bool = False

    # --- Computed ---------------------------------------------------------
    def resolved_audit_url(self) -> str:
        """Return the SQLAlchemy URL for the audit store.

        Priority order:
          1. explicit ``FHIR_MCP_AUDIT_URL``
          2. ``SUPABASE_DB_URL`` if set (Supabase Postgres)
          3. local SQLite under ``state_dir/audit.db``
        """
        if self.audit_url:
            return self.audit_url
        if self.supabase_db_url:
            return _ensure_asyncpg(self.supabase_db_url)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.state_dir / 'audit.db'}"

    def vault_url(self) -> str:
        """Return the SQLAlchemy URL for the PHI vault.

        Same priority as audit, but with a separate explicit override
        (``FHIR_MCP_VAULT_URL``) for operators who want vault and audit on
        different stores (eg. vault on a private Postgres, audit on Supabase).
        """
        if self.vault_url_override:
            return self.vault_url_override
        if self.supabase_db_url:
            return _ensure_asyncpg(self.supabase_db_url)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.state_dir / 'phi_vault.db'}"

    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def cloud_storage_active(self) -> bool:
        return bool(self.audit_url and not self.audit_url.startswith("sqlite")) or bool(
            self.supabase_db_url
        )


def _ensure_asyncpg(url: str) -> str:
    """Rewrite a bare ``postgresql://...`` URL to ``postgresql+asyncpg://...``."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
