"""CDS Hooks client — invokes the mock service or a real CDS Hooks endpoint.

Mock-first is intentional: it keeps eval scoreboards reproducible and avoids
brittle dependencies on external sandboxes. Set ``cds_hooks_url`` and
``allow_live=True`` to call a real service.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from ..config import get_settings
from .mock_service import MOCK_HOOKS, mock_invoke

log = structlog.get_logger(__name__)


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
    ) -> dict[str, Any]:
        """Invoke a CDS Hooks service. Returns the ``{"cards": [...]}`` response."""
        if not allow_live:
            return mock_invoke(hook, context, prefetch)

        url = f"{self._base}/cds-services/fhir-mcp-{hook}"
        payload = {
            "hookInstance": context.get("hookInstance", "fhir-mcp-instance"),
            "hook": hook,
            "context": context,
            "prefetch": prefetch or {},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(url, json=payload)
            if resp.status_code >= 400:
                log.warning("cds_hooks_live_failed", status=resp.status_code, hook=hook)
                return mock_invoke(hook, context, prefetch)
            return resp.json()
        except httpx.HTTPError as exc:
            log.warning("cds_hooks_live_error", error=str(exc), hook=hook)
            return mock_invoke(hook, context, prefetch)


_client: CdsHooksClient | None = None


def get_client() -> CdsHooksClient:
    global _client
    if _client is None:
        _client = CdsHooksClient()
    return _client


def reset_client_for_tests() -> None:
    global _client
    _client = None
