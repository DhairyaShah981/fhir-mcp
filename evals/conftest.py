"""Shared eval fixtures.

The judge LLM is pinned here. If you bump the model, record the score delta
in CHANGELOG.md — the scoreboard isn't comparable across judge models.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

JUDGE_MODEL = os.environ.get("FHIR_MCP_EVAL_JUDGE_MODEL", "claude-sonnet-4-6")
GOLDEN_DIR = Path(__file__).parent / "golden"
JUDGE_DIR = Path(__file__).parent / "judge"


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture(scope="session")
def judge_dir() -> Path:
    return JUDGE_DIR


@pytest.fixture(scope="session")
def judge_model() -> str:
    return JUDGE_MODEL


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Each test runs against a fresh ~/.fhir-mcp state dir so audit/vault don't bleed."""
    monkeypatch.setenv("FHIR_MCP_STATE_DIR", str(tmp_path / "state"))
    # Force config + backend + vault to reload with the new state dir.
    from fhir_mcp import config as _config
    from fhir_mcp import fhir_client as _client
    from fhir_mcp.deid import vault as _vault

    _config.reset_settings_for_tests()
    _client.reset_backend_for_tests()
    _vault.reset_vault_for_tests()
    yield
    _config.reset_settings_for_tests()
    _client.reset_backend_for_tests()
    _vault.reset_vault_for_tests()
