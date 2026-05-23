"""CDS Hooks client — invokes the mock service or a real CDS Hooks endpoint.

Mock-first is intentional: it keeps eval scoreboards reproducible and avoids
brittle dependencies on external sandboxes. Set ``cds_hooks_url`` and
``allow_live=True`` to call a real service.

Returns ``CdsHooksResponse`` so callers know whether the response came from
the live endpoint or from the offline mock (e.g. on live failure → fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from ..config import get_settings
from ..deid.pipeline import deidentify_resource
from .mock_service import MOCK_HOOKS, mock_invoke

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CdsHooksResponse:
    cards: list[dict[str, Any]]
    used_mock: bool
    fallback_reason: str | None = None  # populated when live failed → mock


async def _deid_prefetch(prefetch: dict[str, Any]) -> dict[str, Any]:
    """De-identify every FHIR resource in the prefetch before wire egress.

    Live CDS Hooks endpoints are third parties. Sending raw PHI to them would
    contradict the egress-de-id contract advertised in the README. We walk every
    list/Bundle and rewrite each resource through the de-id pipeline.
    """
    out: dict[str, Any] = {}
    for key, value in prefetch.items():
        if isinstance(value, list):
            out[key] = [
                await deidentify_resource(r) if isinstance(r, dict) else r for r in value
            ]
        elif isinstance(value, dict) and value.get("resourceType") == "Bundle":
            entries = value.get("entry") or []
            new_entries = []
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict):
                    new_entries.append(
                        {**entry, "resource": await deidentify_resource(entry["resource"])}
                    )
                else:
                    new_entries.append(entry)
            out[key] = {**value, "entry": new_entries}
        elif isinstance(value, dict):
            out[key] = await deidentify_resource(value) if value.get("resourceType") else value
        else:
            out[key] = value
    return out


class CdsHooksClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 10.0) -> None:
        self._base = (base_url or get_settings().cds_hooks_url).rstrip("/")
        self._timeout = timeout

    def discovery(self) -> dict[str, Any]:
        """Return the offline-mock discovery document."""
        services = []
        for hook_id, meta in MOCK_HOOKS.items():
            services.append(
                {
                    "hook": hook_id,
                    "id": f"fhir-mcp-{hook_id}",
                    "title": meta["title"],
                    "description": meta["description"],
                }
            )
        return {"services": services}

    async def invoke(
        self,
        hook: str,
        context: dict[str, Any],
        prefetch: dict[str, Any] | None = None,
        *,
        allow_live: bool = False,
    ) -> CdsHooksResponse:
        """Invoke a CDS Hooks service.

        When ``allow_live=True`` we de-identify every resource in ``prefetch``
        before posting it to the third-party endpoint, then fall back to the
        offline mock on any failure (and report it).
        """
        if not allow_live:
            return CdsHooksResponse(cards=mock_invoke(hook, context, prefetch).get("cards", []),
                                    used_mock=True)

        safe_prefetch = await _deid_prefetch(prefetch or {})
        url = f"{self._base}/cds-services/fhir-mcp-{hook}"
        payload = {
            "hookInstance": context.get("hookInstance", "fhir-mcp-instance"),
            "hook": hook,
            "context": context,
            "prefetch": safe_prefetch,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(url, json=payload)
            if resp.status_code >= 400:
                log.warning("cds_hooks_live_failed", status=resp.status_code, hook=hook)
                return CdsHooksResponse(
                    cards=mock_invoke(hook, context, prefetch).get("cards", []),
                    used_mock=True,
                    fallback_reason=f"http_{resp.status_code}",
                )
            return CdsHooksResponse(cards=resp.json().get("cards", []), used_mock=False)
        except httpx.HTTPError as exc:
            log.warning("cds_hooks_live_error", error=str(exc), hook=hook)
            return CdsHooksResponse(
                cards=mock_invoke(hook, context, prefetch).get("cards", []),
                used_mock=True,
                fallback_reason=f"http_error:{type(exc).__name__}",
            )


_client: CdsHooksClient | None = None


def get_client() -> CdsHooksClient:
    global _client
    if _client is None:
        _client = CdsHooksClient()
    return _client


def reset_client_for_tests() -> None:
    global _client
    _client = None
