"""Config routing tests for cloud (Postgres) mode."""

from __future__ import annotations

from fhir_mcp import config as config_mod
from fhir_mcp.config import _ensure_asyncpg, get_settings


def test_local_default_uses_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FHIR_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    config_mod.reset_settings_for_tests()
    s = get_settings()
    assert s.resolved_audit_url().startswith("sqlite+aiosqlite:")
    assert s.vault_url().startswith("sqlite+aiosqlite:")
    assert s.cloud_storage_active() is False


def test_supabase_db_url_routes_audit_and_vault(monkeypatch) -> None:
    monkeypatch.setenv(
        "SUPABASE_DB_URL", "postgresql://postgres:pw@db.example.supabase.co:5432/postgres"
    )
    monkeypatch.delenv("FHIR_MCP_VAULT_URL", raising=False)
    config_mod.reset_settings_for_tests()
    s = get_settings()
    assert s.resolved_audit_url().startswith("postgresql+asyncpg://")
    assert s.vault_url().startswith("postgresql+asyncpg://")
    assert s.cloud_storage_active() is True


def test_vault_url_override_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv(
        "SUPABASE_DB_URL", "postgresql://postgres:pw@db.example.supabase.co:5432/postgres"
    )
    monkeypatch.setenv("FHIR_MCP_VAULT_URL", "sqlite+aiosqlite:///tmp/vault.db")
    config_mod.reset_settings_for_tests()
    s = get_settings()
    assert s.vault_url() == "sqlite+aiosqlite:///tmp/vault.db"
    # Audit still goes to Postgres.
    assert s.resolved_audit_url().startswith("postgresql+asyncpg://")


def test_audit_url_override_takes_precedence(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FHIR_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FHIR_MCP_AUDIT_URL", "sqlite+aiosqlite:////tmp/custom_audit.db")
    monkeypatch.setenv(
        "SUPABASE_DB_URL", "postgresql://postgres:pw@db.example.supabase.co:5432/postgres"
    )
    config_mod.reset_settings_for_tests()
    s = get_settings()
    assert s.resolved_audit_url() == "sqlite+aiosqlite:////tmp/custom_audit.db"


def test_ensure_asyncpg_normalizes_postgres_scheme() -> None:
    assert _ensure_asyncpg("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert _ensure_asyncpg("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    # Already asyncpg — left alone.
    assert (
        _ensure_asyncpg("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    )
