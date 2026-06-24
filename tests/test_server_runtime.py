"""Cover server.run_stdio / run_sse / _configure_logging without blocking."""

from __future__ import annotations

import logging

from fhir_mcp import server as server_mod


def test_configure_logging_adds_handler_idempotent() -> None:
    # Ensure stale handlers from earlier tests don't trip the check.
    server_mod._configure_logging()
    server_mod._configure_logging()
    handlers = [h for h in logging.getLogger().handlers if getattr(h, "_fhir_mcp", False)]
    assert len(handlers) == 1  # idempotent


def test_run_stdio_invokes_app_run(monkeypatch) -> None:
    started: list[str] = []

    class _FakeApp:
        def run(self, transport: str) -> None:
            started.append(transport)

    monkeypatch.setattr(server_mod, "_build_app", lambda: _FakeApp())

    import anyio
    monkeypatch.setattr(anyio, "run", lambda fn, *a, **k: None)
    server_mod.run_stdio()
    assert started == ["stdio"]


def test_run_sse_sets_host_port_and_runs(monkeypatch) -> None:
    started: list[tuple[str, str, int]] = []

    class _Settings:
        host = ""
        port = 0

    class _FakeApp:
        def __init__(self) -> None:
            self.settings = _Settings()

        def custom_route(self, _path, methods=None):
            # FastMCP exposes custom_route as a decorator; the test just records it.
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, transport: str) -> None:
            started.append((transport, self.settings.host, self.settings.port))

    monkeypatch.setattr(server_mod, "_build_app", lambda: _FakeApp())
    import anyio
    monkeypatch.setattr(anyio, "run", lambda fn, *a, **k: None)
    server_mod.run_sse(host="127.0.0.1", port=12345)
    assert started == [("sse", "127.0.0.1", 12345)]
