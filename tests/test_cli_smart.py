"""Tests for the SMART-related CLI subcommands."""

from __future__ import annotations

from typer.testing import CliRunner

from fhir_mcp.cli import app

runner = CliRunner()


def _config_dict():
    return {
        "authorization_endpoint": "https://issuer.example/auth",
        "token_endpoint": "https://issuer.example/token",
        "capabilities": ["launch-standalone"],
        "scopes_supported": ["patient/*.read"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def test_smart_discover(monkeypatch) -> None:
    from fhir_mcp.auth import smart as smart_mod

    async def fake_discover(iss: str):
        return smart_mod.SmartConfig.from_dict(iss, _config_dict())

    monkeypatch.setattr(smart_mod, "discover", fake_discover)
    result = runner.invoke(app, ["smart-discover", "https://issuer.example/fhir"])
    assert result.exit_code == 0
    assert "authorization_endpoint" in result.stdout
    assert "issuer.example" in result.stdout


def test_smart_authorize_url_requires_config(monkeypatch) -> None:
    monkeypatch.delenv("FHIR_MCP_SMART_ISSUER", raising=False)
    monkeypatch.delenv("FHIR_MCP_SMART_CLIENT_ID", raising=False)
    from fhir_mcp import config as config_mod

    config_mod.reset_settings_for_tests()
    result = runner.invoke(app, ["smart-authorize-url"])
    assert result.exit_code == 2


def test_smart_authorize_url_with_flags(monkeypatch) -> None:
    from fhir_mcp.auth import smart as smart_mod

    async def fake_discover(iss: str):
        return smart_mod.SmartConfig.from_dict(iss, _config_dict())

    monkeypatch.setattr(smart_mod, "discover", fake_discover)
    result = runner.invoke(
        app,
        [
            "smart-authorize-url",
            "--issuer", "https://issuer.example/fhir",
            "--client-id", "test-client",
            "--scopes", "patient/*.read fhir-mcp/reid",
        ],
    )
    assert result.exit_code == 0, result.stdout
    flat = result.stdout.replace("\n", "").replace(" ", "")
    assert "client_id=test-client" in flat
    assert "code_challenge_method=S256" in flat
    assert "response_type=code" in flat


def test_smart_jwt_requires_keys(monkeypatch) -> None:
    monkeypatch.delenv("FHIR_MCP_SMART_ISSUER", raising=False)
    monkeypatch.delenv("FHIR_MCP_SMART_CLIENT_ID", raising=False)
    from fhir_mcp import config as config_mod

    config_mod.reset_settings_for_tests()
    result = runner.invoke(app, ["smart-jwt"])
    assert result.exit_code == 2
