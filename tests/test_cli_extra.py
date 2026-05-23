"""Additional CLI coverage — serve dispatch + eval subcommand + reid success."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fhir_mcp.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_serve_stdio_dispatches_run_stdio(runner: CliRunner, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("fhir_mcp.server.run_stdio", lambda: calls.append("stdio"))
    monkeypatch.setattr("fhir_mcp.server.run_sse", lambda host, port: calls.append(f"sse:{host}:{port}"))
    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code == 0
    assert calls == ["stdio"]


def test_serve_sse_dispatches_run_sse(runner: CliRunner, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("fhir_mcp.server.run_stdio", lambda: calls.append("stdio"))
    monkeypatch.setattr("fhir_mcp.server.run_sse", lambda host, port: calls.append(f"sse:{host}:{port}"))
    result = runner.invoke(app, ["serve", "--transport", "sse", "--host", "0.0.0.0", "--port", "9000"])
    assert result.exit_code == 0
    assert calls == ["sse:0.0.0.0:9000"]


def test_reid_success_via_cli(runner: CliRunner, monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "k")
    from fhir_mcp import config as config_mod
    from fhir_mcp.deid.pipeline import deidentify_resource

    config_mod.reset_settings_for_tests()

    # Seed the vault by running de-id on a resource synchronously.
    import asyncio

    deid = asyncio.run(
        deidentify_resource(
            {"resourceType": "Patient", "id": "cli-reid-1", "name": [{"family": "CliTest"}]}
        )
    )
    pseudo = deid["name"][0]["family"]
    result = runner.invoke(app, ["reid", pseudo, "--reason", "test"])
    assert result.exit_code == 0
    assert "CliTest" in result.stdout


def test_reid_not_found_via_cli(runner: CliRunner, monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "k")
    from fhir_mcp import config as config_mod

    config_mod.reset_settings_for_tests()
    result = runner.invoke(app, ["reid", "FM_does_not_exist_pseudo"])
    assert result.exit_code == 4


def test_eval_subcommand_renders_scoreboard(runner: CliRunner, monkeypatch, tmp_path) -> None:
    """`fhir-mcp eval` runs pytest and renders the report; mock both."""
    monkeypatch.setattr(
        "subprocess.call",
        lambda *_a, **_kw: 0,  # pretend pytest passed
    )
    from pathlib import Path
    monkeypatch.setattr(
        "evals.report.render_report", lambda out: Path(out).write_text("ok\n")
    )
    out_path = tmp_path / "scoreboard.md"
    result = runner.invoke(app, ["eval", "--report-path", str(out_path)])
    assert result.exit_code == 0
    assert out_path.read_text() == "ok\n"
