"""HAPI public-test-server backend over HTTPX.

Only read interactions are exposed in v0.1. Writes against HAPI are out of scope
to preserve the "no surprises" contract for evaluators.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)

# Hard upper bound on $everything pagination to keep tool outputs LLM-friendly.
_EVERYTHING_DEFAULT_CAP = 200


class HapiBackend:
    name: str = "hapi"

    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        everything_cap: int = _EVERYTHING_DEFAULT_CAP,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._everything_cap = everything_cap
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"Accept": "application/fhir+json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, resource_type: str, params: dict[str, str | int]) -> list[dict]:
        """Map our convenience params back to FHIR search semantics.

        FHIR allows multiple ``date=`` query params with different comparators
        (e.g. ``?date=ge2024-01-01&date=le2024-12-31`` for a closed range).
        We emit them as a real list so httpx URL-encodes both correctly.
        """
        outgoing: list[tuple[str, str]] = []
        for key, value in params.items():
            if key == "date_ge":
                outgoing.append(("date", f"ge{value}"))
            elif key == "date_le":
                outgoing.append(("date", f"le{value}"))
            else:
                outgoing.append((key, str(value)))

        # httpx accepts a sequence of (str, str) pairs at runtime — the type
        # hint expects PrimitiveData which is the same thing in practice.
        resp = await self._client.get(f"/{resource_type}", params=outgoing)  # type: ignore[arg-type]
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
        """Patient/$everything with a hard cap to protect LLM context windows."""
        resp = await self._client.get(
            f"/Patient/{patient_id}/$everything",
            params={"_count": self._everything_cap},
        )
        resp.raise_for_status()
        bundle = resp.json()
        resources = [
            e.get("resource", {}) for e in bundle.get("entry", []) if e.get("resource")
        ]
        if len(resources) >= self._everything_cap:
            log.warning(
                "hapi_everything_truncated",
                patient_id=patient_id,
                cap=self._everything_cap,
                returned=len(resources),
            )
        return resources[: self._everything_cap]
