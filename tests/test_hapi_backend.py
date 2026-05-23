"""Unit tests for the HAPI backend with mocked httpx."""

from __future__ import annotations

import httpx
import pytest

from fhir_mcp.backends.hapi import HapiBackend


class _MockResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code != 404:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    def __init__(self, scripted: dict[str, _MockResp]) -> None:
        self._scripted = scripted
        self.calls: list[tuple[str, object]] = []

    async def get(self, path: str, params: object = None) -> _MockResp:
        self.calls.append((path, params))
        return self._scripted.get(path) or _MockResp({"entry": []})

    async def aclose(self) -> None:
        return None


def _build(scripted: dict[str, _MockResp]) -> HapiBackend:
    backend = HapiBackend(base_url="https://example.invalid")
    backend._client = _RecordingClient(scripted)  # type: ignore[assignment]
    return backend


@pytest.mark.asyncio
async def test_search_maps_date_filters_to_fhir_syntax() -> None:
    scripted = {
        "/Observation": _MockResp(
            {"entry": [{"resource": {"resourceType": "Observation", "id": "o1"}}]}
        )
    }
    backend = _build(scripted)
    results = await backend.search(
        "Observation", {"date_ge": "2025-01-01", "date_le": "2025-12-31", "_count": 5}
    )
    assert len(results) == 1
    _, params = backend._client.calls[0]  # type: ignore[attr-defined]
    # Params must now be a list of tuples so both date comparators survive.
    assert isinstance(params, list)
    date_params = [v for k, v in params if k == "date"]
    assert "ge2025-01-01" in date_params
    assert "le2025-12-31" in date_params
    assert any(k == "_count" for k, _ in params)


@pytest.mark.asyncio
async def test_everything_emits_count_cap_to_protect_context() -> None:
    """$everything must paginate; tools can't ship 10k resources to the LLM."""
    big = {"entry": [{"resource": {"resourceType": "Observation", "id": f"o{i}"}} for i in range(250)]}
    backend = HapiBackend(base_url="https://example.invalid", everything_cap=50)
    backend._client = _RecordingClient({"/Patient/p1/$everything": _MockResp(big)})  # type: ignore[assignment]
    results = await backend.everything("p1")
    assert len(results) == 50  # capped, not 250
    _, params = backend._client.calls[0]  # type: ignore[attr-defined]
    assert any(k == "_count" for k, _ in params) if isinstance(params, list) else params.get("_count") == 50


@pytest.mark.asyncio
async def test_read_returns_none_on_404() -> None:
    backend = _build({"/Patient/missing": _MockResp({"issue": []}, status=404)})
    result = await backend.read("Patient", "missing")
    assert result is None


@pytest.mark.asyncio
async def test_read_returns_resource_on_200() -> None:
    backend = _build({"/Patient/p1": _MockResp({"resourceType": "Patient", "id": "p1"})})
    result = await backend.read("Patient", "p1")
    assert result is not None
    assert result["id"] == "p1"


@pytest.mark.asyncio
async def test_everything_returns_all_entries() -> None:
    backend = _build(
        {
            "/Patient/p1/$everything": _MockResp(
                {
                    "entry": [
                        {"resource": {"resourceType": "Patient", "id": "p1"}},
                        {"resource": {"resourceType": "Observation", "id": "o1"}},
                    ]
                }
            )
        }
    )
    results = await backend.everything("p1")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_aclose_is_safe() -> None:
    backend = _build({})
    await backend.aclose()
