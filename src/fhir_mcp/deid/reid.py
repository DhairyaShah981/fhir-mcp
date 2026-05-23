"""Re-identification — the privileged audit-emitting path.

Re-id is **off** by default. To enable it, both must be true:

1. ``FHIR_MCP_ENABLE_REID=true``
2. The caller supplies the matching ``FHIR_MCP_REID_KEY`` via the ``key``
   argument to ``reidentify``.

Every successful re-id call writes an ``audit_events`` row tagged
``tool='reid'`` with the requested pseudonyms in ``resource_refs``.
"""

from __future__ import annotations

import structlog

from ..audit import record as audit_record
from ..config import get_settings
from .vault import get_vault

log = structlog.get_logger(__name__)


class ReidDisabledError(RuntimeError):
    """Re-identification is not enabled on this server."""


class ReidUnauthorizedError(RuntimeError):
    """The supplied re-id key did not match."""


class ReidNotFoundError(KeyError):
    """No vault entry exists for the requested pseudonym."""


async def reidentify(pseudonym: str, *, key: str, actor: str = "local", reason: str = "") -> str:
    """Resolve a pseudonym → its original PHI value. Audited."""
    settings = get_settings()
    if not settings.enable_reid:
        raise ReidDisabledError("Re-identification is disabled. Set FHIR_MCP_ENABLE_REID=true.")
    if not settings.reid_key or key != settings.reid_key:
        await audit_record(
            tool="reid",
            args={"pseudonym": pseudonym, "reason": reason},
            outcome="unauthorized",
            actor=actor,
        )
        raise ReidUnauthorizedError("Invalid re-identification key.")

    vault = get_vault()
    original = await vault.lookup(pseudonym)
    if original is None:
        await audit_record(
            tool="reid",
            args={"pseudonym": pseudonym, "reason": reason},
            outcome="not_found",
            actor=actor,
        )
        raise ReidNotFoundError(pseudonym)

    await audit_record(
        tool="reid",
        args={"pseudonym": pseudonym, "reason": reason},
        resource_refs=[pseudonym],
        outcome="ok",
        actor=actor,
    )
    return original
