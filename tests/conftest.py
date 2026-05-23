"""Per-test isolation of audit + vault state."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FHIR_MCP_STATE_DIR", str(tmp_path / "state"))
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
