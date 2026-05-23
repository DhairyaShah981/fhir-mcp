"""Unit tests for the CDS Hooks client (discovery + live with mock httpx)."""

from __future__ import annotations

import httpx

from fhir_mcp.cds_hooks.client import CdsHooksClient, _deid_prefetch


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
    severities = [card.get("indicator") for card in resp.cards]
    assert "critical" in severities
    assert resp.used_mock is True
    assert resp.fallback_reason == "http_404"


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
    assert resp.used_mock is True
    assert resp.fallback_reason is not None
    assert "http_error" in resp.fallback_reason


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
    assert resp.cards[0]["summary"] == "live!"
    assert resp.used_mock is False
    assert resp.fallback_reason is None


async def test_invoke_mock_path_returns_used_mock_true() -> None:
    """Default allow_live=False must report used_mock=True."""
    c = CdsHooksClient()
    resp = await c.invoke("patient-view", context={}, prefetch={})
    assert resp.used_mock is True
    assert resp.fallback_reason is None


# --- new: prefetch de-identification on the live path ---------------------


async def test_live_prefetch_is_deidentified_before_posting(monkeypatch) -> None:
    """The single most important contract: live CDS calls must not leak source PHI."""
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"cards": []}

    class _RecordingClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return None

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    c = CdsHooksClient(base_url="https://example.invalid")

    raw_patient = {
        "resourceType": "Patient",
        "id": "live-test-patient",
        "name": [{"family": "Doelivetest", "given": ["Janicelivetest"]}],
        "telecom": [{"system": "phone", "value": "(617) 555-9821"}],
        "birthDate": "1972-03-04",
    }
    raw_med = {
        "resourceType": "MedicationStatement",
        "id": "live-test-med",
        "medicationCodeableConcept": {"text": "Warfarin 5mg"},
        "subject": {"reference": "Patient/live-test-patient"},
    }
    await c.invoke(
        "medication-prescribe",
        context={},
        prefetch={"patient": raw_patient, "medications": [raw_med]},
        allow_live=True,
    )

    import json as _json
    serialized = _json.dumps(captured["json"])
    for token in ("Doelivetest", "Janicelivetest", "(617) 555-9821", "1972-03-04", "live-test-patient"):
        assert token not in serialized, f"PHI token {token!r} leaked into CDS Hooks request body"


async def test_deid_prefetch_handles_bundle_shape() -> None:
    """Bundles in prefetch (FHIR canonical) must also be walked."""
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "bundle-test-pt",
                    "name": [{"family": "Bundlefamily"}],
                }
            }
        ],
    }
    out = await _deid_prefetch({"patient": bundle})
    serialized = str(out)
    assert "Bundlefamily" not in serialized
    assert "bundle-test-pt" not in serialized


async def test_deid_prefetch_passes_through_non_resources() -> None:
    """Scalars/empty values are passed through unchanged."""
    out = await _deid_prefetch({"foo": "bar", "n": 42, "empty": None})
    assert out == {"foo": "bar", "n": 42, "empty": None}
