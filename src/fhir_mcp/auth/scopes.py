"""SMART scope parsing.

Spec: http://hl7.org/fhir/smart-app-launch/scopes-and-launch-context.html

We deliberately keep this small — only the parts needed to gate the
re-identification path and tell the MCP client what it's allowed to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Custom fhir-mcp scope: re-identification is only allowed if the SMART token
# carries this scope (in addition to whatever patient/system scopes it has).
REID_SCOPE = "fhir-mcp/reid"

_SCOPE_RE = re.compile(
    r"^(?P<level>patient|user|system|fhir-mcp)/(?P<resource>[*\w-]+)\.(?P<rights>\*|read|write|[crudsa]+)$"
)

# SMART v1 right-word → set of v2 letters.
_V1_TO_V2 = {
    "read": "rs",
    "write": "cud",
    "*": "crudsa",
}


@dataclass(frozen=True)
class SmartScope:
    """A parsed SMART scope, e.g. ``patient/Observation.read`` or ``patient/*.rs``."""

    level: Literal["patient", "user", "system", "fhir-mcp"]
    resource: str
    rights: str  # v1 word ("read"/"write"/"*") or v2 letter combo ("crudsa")

    @property
    def canonical(self) -> str:
        return f"{self.level}/{self.resource}.{self.rights}"

    @property
    def v2_rights(self) -> str:
        return _V1_TO_V2.get(self.rights, self.rights)

    def grants(self, level: str, resource: str, right: str) -> bool:
        if self.level != level:
            return False
        if self.resource not in {resource, "*"}:
            return False
        rights = self.v2_rights
        return right in rights or "a" in rights


@dataclass(frozen=True)
class ScopeSet:
    """A bundle of SMART scopes attached to a token."""

    scopes: frozenset[str]

    def has_raw(self, raw: str) -> bool:
        return raw in self.scopes

    def allows(self, level: str, resource: str, right: str) -> bool:
        for raw in self.scopes:
            scope = _parse_one(raw)
            if scope and scope.grants(level, resource, right):
                return True
        return False

    def can_reidentify(self) -> bool:
        return REID_SCOPE in self.scopes


def _parse_one(raw: str) -> SmartScope | None:
    m = _SCOPE_RE.match(raw.strip())
    if not m:
        return None
    return SmartScope(level=m["level"], resource=m["resource"], rights=m["rights"])  # type: ignore[arg-type]


def parse_scopes(scope_string: str) -> ScopeSet:
    """Parse a space-separated SMART scope string. Non-standard tokens preserved verbatim."""
    parts = [p for p in (scope_string or "").split() if p]
    return ScopeSet(scopes=frozenset(parts))
