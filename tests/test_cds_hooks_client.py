"""Unit tests for the CDS Hooks client (discovery + live with mock httpx)."""

from __future__ import annotations

import httpx

from fhir_mcp.cds_hooks.client import CdsHooksClient


def test_discovery_lists_all_mock_hooks() -> None:
    disc = CdsHooksClient().discovery()
    hook_ids = {s["hook"] for s in disc["services"]}
    assert {"patient-view", "medication-prescribe", "order-select"}.issubset(hook_ids)


async def test_invoke_with_allow_live_falls_back_on_404(monkeypatch) -> None:
    class _Resp:
        status_code = 404

        def json(self) -> dict:
            return {}

    class _MockClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return None

        async def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    c = CdsHooksClient(base_url="https://example.invalid")
    resp = await c.invoke(
        "medication-prescribe",
        context={
            "draftOrders": {
                "entry": [
                    {"resource": {"medicationCodeableConcept": {"text": "Amoxicillin"}}}
                ]
            }
        },
        prefetch={"allergies": [{"code": {"text": "penicillin"}}], "medications": []},
        allow_live=True,
    )
    # Live failed → mock fallback fires the critical allergy card.
    severities = [c.get("indicator") for c in resp.get("cards", [])]
    assert "critical" in severities


async def test_invoke_with_allow_live_handles_network_error(monkeypatch) -> None:
    class _Boom:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return None

        async def post(self, *_a, **_k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    c = CdsHooksClient(base_url="https://example.invalid")
    resp = await c.invoke("patient-view", context={}, prefetch={}, allow_live=True)
    assert "cards" in resp  # mock fallback always returns a cards key


async def test_invoke_with_live_success(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"cards": [{"indicator": "info", "summary": "live!"}]}

    class _MockClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return None

        async def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    c = CdsHooksClient(base_url="https://example.invalid")
    resp = await c.invoke("patient-view", context={}, prefetch={}, allow_live=True)
    assert resp["cards"][0]["summary"] == "live!"
