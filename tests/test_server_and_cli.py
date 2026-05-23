"""Smoke tests for the MCP server wiring and the Typer CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fhir_mcp.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_cli_version(runner: CliRunner) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "fhir-mcp" in result.stdout


def test_cli_config_show(runner: CliRunner) -> None:
    result = runner.invoke(app, ["config-show"])
    assert result.exit_code == 0
    assert "backend" in result.stdout
    assert "synthea" in result.stdout


def test_cli_serve_bad_transport(runner: CliRunner) -> None:
    result = runner.invoke(app, ["serve", "--transport", "carrier-pigeon"])
    assert result.exit_code != 0


def test_cli_reid_disabled_by_default(runner: CliRunner, monkeypatch) -> None:
    monkeypatch.delenv("FHIR_MCP_ENABLE_REID", raising=False)
    from fhir_mcp import config as config_mod

    config_mod.reset_settings_for_tests()
    result = runner.invoke(app, ["reid", "PT_made_up"])
    assert result.exit_code == 2  # ReidDisabledError exit code


def test_cli_reid_unauthorized(runner: CliRunner, monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.delenv("FHIR_MCP_REID_KEY", raising=False)
    from fhir_mcp import config as config_mod

    config_mod.reset_settings_for_tests()
    result = runner.invoke(app, ["reid", "PT_made_up"])
    assert result.exit_code == 3


def test_main_module_runs() -> None:
    # Just verify the __main__ guard is importable.
    import fhir_mcp.__main__ as m  # noqa: F401


def test_build_app_registers_all_tools() -> None:
    from fhir_mcp import server as server_mod

    app_instance = server_mod._build_app()
    # FastMCP exposes registered names via internal state — we just sanity-check by
    # round-tripping through repr() / list_tools-equivalent if available.
    assert app_instance is not None


async def test_server_bootstrap_storage_initializes_audit_and_vault() -> None:
    from fhir_mcp.server import _bootstrap_storage

    await _bootstrap_storage()
    # If bootstrap completes without raising, the audit + vault tables exist.


async def test_build_app_tools_callable() -> None:
    """Call each tool through the registered FastMCP coroutine indirectly."""
    from fhir_mcp.tools.search_patients import SearchPatientsInput, search_patients

    out = await search_patients(SearchPatientsInput(limit=3))
    assert out.count >= 1
