"""HAPI public-test-server backend over HTTPX.

Only read interactions are exposed in v0.1. Writes against HAPI are out of scope
to preserve the "no surprises" contract for evaluators.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)


class HapiBackend:
    name: str = "hapi"

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"Accept": "application/fhir+json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, resource_type: str, params: dict[str, str | int]) -> list[dict]:
        # Map our convenience params back to FHIR search semantics.
        outgoing: dict[str, str] = {}
        for key, value in params.items():
            if key == "date_ge":
                outgoing.setdefault("date", f"ge{value}")
            elif key == "date_le":
                outgoing["date"] = (outgoing.get("date", "") + f"&date=le{value}").lstrip("&")
            else:
                outgoing[key] = str(value)

        resp = await self._client.get(f"/{resource_type}", params=outgoing)
        resp.raise_for_status()
        bundle = resp.json()
        return [e.get("resource", {}) for e in bundle.get("entry", []) if e.get("resource")]

    async def read(self, resource_type: str, resource_id: str) -> dict | None:
        resp = await self._client.get(f"/{resource_type}/{resource_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def everything(self, patient_id: str) -> list[dict]:
        resp = await self._client.get(f"/Patient/{patient_id}/$everything")
        resp.raise_for_status()
        bundle = resp.json()
        return [e.get("resource", {}) for e in bundle.get("entry", []) if e.get("resource")]
