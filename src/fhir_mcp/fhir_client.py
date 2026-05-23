"""Backend-agnostic FHIR R4 client.

We deliberately avoid binding tightly to ``fhirclient`` resource classes — Synthea
bundles and HAPI responses are both JSON, so we treat resources as dicts and only
parse with Pydantic at the tool boundary. This keeps the in-memory backend free
of any network dependency at import time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import get_settings


@runtime_checkable
class FhirBackend(Protocol):
    """Read-only FHIR R4 interactions.

    Concrete implementations set ``name`` to a backend label ("synthea", "hapi").
    """

    name: str

    async def search(self, resource_type: str, params: dict[str, str | int]) -> list[dict]:
        """FHIR search interaction. Returns the matching resources (entries' .resource)."""
        ...

    async def read(self, resource_type: str, resource_id: str) -> dict | None:
        """FHIR read interaction. Returns ``None`` if not found."""
        ...

    async def everything(self, patient_id: str) -> list[dict]:
        """Patient/$everything — all resources referencing this patient."""
        ...


_backend_cache: FhirBackend | None = None


def get_backend() -> FhirBackend:
    """Lazily construct + cache the configured backend."""
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache

    settings = get_settings()
    if settings.backend == "synthea":
        from .backends.synthea import SyntheaBackend

        _backend_cache = SyntheaBackend()
    elif settings.backend == "hapi":
        from .backends.hapi import HapiBackend

        _backend_cache = HapiBackend(base_url=settings.hapi_base_url)
    else:  # pragma: no cover — exhaustively handled above
        raise ValueError(f"Unknown FHIR backend: {settings.backend}")
    return _backend_cache


def reset_backend_for_tests() -> None:
    global _backend_cache
    _backend_cache = None
