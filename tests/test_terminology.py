"""Unit tests for the terminology module (offline + live path with mock)."""

from __future__ import annotations

import httpx

from fhir_mcp import terminology


def test_normalize_system_short_name() -> None:
    assert terminology.normalize_system("loinc") == "loinc"
    assert terminology.normalize_system("snomed") == "snomed"


def test_normalize_system_canonical_uri() -> None:
    assert terminology.normalize_system("http://snomed.info/sct") == "snomed"
    assert terminology.normalize_system("http://loinc.org") == "loinc"


def test_normalize_system_unknown() -> None:
    assert terminology.normalize_system("not-a-system") is None


def test_system_uri_roundtrip() -> None:
    assert terminology.system_uri("loinc") == "http://loinc.org"


def test_offline_lookup_hit_and_miss() -> None:
    hit = terminology.offline_lookup("loinc", "4548-4")
    assert hit is not None and "Hemoglobin A1c" in hit.display
    assert terminology.offline_lookup("loinc", "zzzz") is None


async def test_live_lookup_success(monkeypatch) -> None:
    """Mock httpx to simulate a successful $lookup response."""

    class _MockResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "parameter": [
                    {"name": "name", "valueString": "MOCK"},
                    {"name": "display", "valueString": "Mocked display"},
                ]
            }

    class _MockClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def get(self, _url, params=None, headers=None):
            return _MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)
    hit = await terminology.live_lookup("loinc", "made-up-code")
    assert hit is not None
    assert hit.display == "Mocked display"


async def test_live_lookup_handles_error(monkeypatch) -> None:
    class _Boom:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def get(self, *_a, **_k):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    hit = await terminology.live_lookup("loinc", "x")
    assert hit is None


async def test_lookup_falls_through_to_live_when_offline_misses(monkeypatch) -> None:
    async def _fake_live(system, code):
        return terminology.CodeLookup(code=code, system=system, display="live")

    monkeypatch.setattr(terminology, "live_lookup", _fake_live)
    hit = await terminology.lookup("loinc", "made-up", allow_live=True)
    assert hit is not None and hit.display == "live"


async def test_lookup_no_live_returns_none_for_unknown() -> None:
    hit = await terminology.lookup("loinc", "made-up", allow_live=False)
    assert hit is None


async def test_lookup_unknown_system() -> None:
    hit = await terminology.lookup("not-a-system", "x")
    assert hit is None
