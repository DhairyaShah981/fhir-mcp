"""Re-identification — the privileged audit-emitting path.

Re-id is **off** by default. To enable it, ``FHIR_MCP_ENABLE_REID=true`` must
be set, AND the caller must supply *one* of:

* A static key matching ``FHIR_MCP_REID_KEY`` (local-mode shortcut), OR
* A SMART-on-FHIR token carrying the ``fhir-mcp/reid`` scope.

Every re-id call writes an ``audit_events`` row tagged ``tool='reid'`` with
the requested pseudonyms in ``resource_refs`` and the trace id of the
invoking MCP tool call.
"""

from __future__ import annotations

import structlog

from ..audit import record as audit_record
from ..auth.scopes import REID_SCOPE
from ..auth.smart import SmartToken
from ..config import get_settings
from .vault import get_vault

log = structlog.get_logger(__name__)


class ReidDisabledError(RuntimeError):
    """Re-identification is not enabled on this server."""


class ReidUnauthorizedError(RuntimeError):
    """Neither the supplied key nor the SMART token authorized this re-id."""


class ReidNotFoundError(KeyError):
    """No vault entry exists for the requested pseudonym."""


def _smart_authorized(token: SmartToken | None) -> bool:
    if token is None:
        return False
    if token.expired:
        return False
    return token.scopes.can_reidentify() or token.scopes.has_raw(REID_SCOPE)


def _smart_actor(token: SmartToken | None, fallback: str) -> str:
    if token is None:
        return fallback
    # Prefer the patient context if present; otherwise fall back to client_id.
    return token.patient or token.raw.get("client_id") or fallback


async def reidentify(
    pseudonym: str,
    *,
    key: str | None = None,
    smart_token: SmartToken | None = None,
    actor: str = "local",
    reason: str = "",
) -> str:
    """Resolve a pseudonym → its original PHI value. Audited.

    Authorization rules (any one of the two paths succeeds):
      1. ``smart_token`` carries the ``fhir-mcp/reid`` scope and is not expired.
      2. ``key`` matches the configured ``FHIR_MCP_REID_KEY``.
    """
    settings = get_settings()
    if not settings.enable_reid:
        raise ReidDisabledError("Re-identification is disabled. Set FHIR_MCP_ENABLE_REID=true.")

    smart_ok = _smart_authorized(smart_token)
    key_ok = bool(settings.reid_key and key and key == settings.reid_key)
    if not (smart_ok or key_ok):
        await audit_record(
            tool="reid",
            args={"pseudonym": pseudonym, "reason": reason, "auth_path": "denied"},
            outcome="unauthorized",
            actor=_smart_actor(smart_token, actor),
        )
        raise ReidUnauthorizedError(
            "Re-identification denied — provide a valid key or a SMART token with fhir-mcp/reid scope."
        )
    auth_path = "smart" if smart_ok else "key"

    vault = get_vault()
    original = await vault.lookup(pseudonym)
    if original is None:
        await audit_record(
            tool="reid",
            args={"pseudonym": pseudonym, "reason": reason, "auth_path": auth_path},
            outcome="not_found",
            actor=_smart_actor(smart_token, actor),
        )
        raise ReidNotFoundError(pseudonym)

    await audit_record(
        tool="reid",
        args={"pseudonym": pseudonym, "reason": reason, "auth_path": auth_path},
        resource_refs=[pseudonym],
        outcome="ok",
        actor=_smart_actor(smart_token, actor),
    )
    return original
