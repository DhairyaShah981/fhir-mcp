"""Extra observability coverage — the Langfuse-enabled span path."""

from __future__ import annotations

import sys
import types

from fhir_mcp import config as config_mod
from fhir_mcp.observability import span


async def test_langfuse_enabled_path_uses_fake_client(monkeypatch) -> None:
    """When the Langfuse env vars are set and import succeeds, span should use the client."""

    class _FakeTrace:
        def __init__(self) -> None:
            self.updates: list[dict] = []

        def update(self, **kwargs) -> None:
            self.updates.append(kwargs)

    class _FakeClient:
        def __init__(self, **_kw) -> None:
            self.created: list[dict] = []

        def trace(self, **kw):
            self.created.append(kw)
            return _FakeTrace()

    fake_mod = types.ModuleType("langfuse")
    fake_mod.Langfuse = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_mod)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    config_mod.reset_settings_for_tests()

    with span("happy-path") as tid:
        assert tid.startswith("tr_")


async def test_langfuse_enabled_path_reports_exception(monkeypatch) -> None:
    """When the wrapped block raises, the trace.update(ERROR) branch should fire."""

    updates: list[dict] = []

    class _FakeTrace:
        def update(self, **kwargs) -> None:
            updates.append(kwargs)

    class _FakeClient:
        def __init__(self, **_kw) -> None:
            pass

        def trace(self, **_kw):
            return _FakeTrace()

    fake_mod = types.ModuleType("langfuse")
    fake_mod.Langfuse = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_mod)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    config_mod.reset_settings_for_tests()

    try:
        with span("boom"):
            raise RuntimeError("kaboom")
    except RuntimeError:
        pass

    assert updates and updates[0]["level"] == "ERROR"
