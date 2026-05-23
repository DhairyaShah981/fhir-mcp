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

    # --- Cloud opt-in (no defaults — must be explicitly set) --------------
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_service_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_KEY")
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")

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
        if self.audit_url:
            return self.audit_url
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.state_dir / 'audit.db'}"

    def vault_url(self) -> str:
        # Vault always lives next to audit data — same backing store.
        if self.supabase_url and self.supabase_service_key:
            # Vault rows live in Supabase when cloud mode is on.
            return self.resolved_audit_url()  # connection re-used; table is separate
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.state_dir / 'phi_vault.db'}"

    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
