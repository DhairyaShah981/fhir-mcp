"""Unit tests for observability — trace context + traced decorator."""

from __future__ import annotations

from fhir_mcp import config as config_mod
from fhir_mcp.observability import current_trace_id, span, traced


async def test_current_trace_id_mints_a_default() -> None:
    tid = current_trace_id()
    assert tid.startswith("tr_")
    # Subsequent calls inside the same context return the same id.
    assert current_trace_id() == tid


async def test_span_sets_and_restores_trace_id() -> None:
    outer = current_trace_id()
    with span("outer") as outer_tid:
        assert outer_tid.startswith("tr_")
        assert current_trace_id() == outer_tid
        with span("inner") as inner_tid:
            assert inner_tid != outer_tid
            assert current_trace_id() == inner_tid
        assert current_trace_id() == outer_tid
    assert current_trace_id() == outer


async def test_traced_decorator_wraps_calls() -> None:
    @traced("decorated")
    async def go(x: int) -> int:
        return x * 2

    assert await go(3) == 6


async def test_span_with_langfuse_disabled_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    config_mod.reset_settings_for_tests()
    with span("noop") as tid:
        assert tid.startswith("tr_")


async def test_span_with_langfuse_missing_import(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "x")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "y")
    config_mod.reset_settings_for_tests()
    # Block the langfuse import so the warning path runs.
    import sys
    monkeypatch.setitem(sys.modules, "langfuse", None)
    with span("import-fails") as tid:
        assert tid.startswith("tr_")
