"""Regex-based PHI recognizers for free-text fields.

For *structured* FHIR fields (Patient.name, telecom, address, identifier, etc.)
we walk known paths in ``pipeline.py``. These recognizers exist for *free-text*
fields (note, comment, text.div, DocumentReference.content).

Presidio is supported as an optional drop-in upgrade (see ``pipeline.py``); the
default regex engine keeps the zero-config quickstart honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PHI_KIND_NAME = "name"
PHI_KIND_MRN = "mrn"
PHI_KIND_DOB = "dob"
PHI_KIND_PHONE = "phone"
PHI_KIND_EMAIL = "email"
PHI_KIND_SSN = "ssn"
PHI_KIND_ADDRESS = "address"


@dataclass(frozen=True)
class _Pattern:
    kind: str
    pattern: re.Pattern[str]


# Conservative patterns — we'd rather over-redact in free text than leak.
_PATTERNS: list[_Pattern] = [
    _Pattern(PHI_KIND_SSN, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    _Pattern(PHI_KIND_PHONE, re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    _Pattern(PHI_KIND_EMAIL, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    _Pattern(PHI_KIND_DOB, re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")),
    _Pattern(PHI_KIND_MRN, re.compile(r"\bMRN[:\s#]*([A-Z0-9-]{5,})\b", re.IGNORECASE)),
]


def find_phi_spans(text: str) -> list[tuple[str, str]]:
    """Return ``(kind, matched_text)`` for every PHI span in the input."""
    found: list[tuple[str, str]] = []
    for p in _PATTERNS:
        for m in p.pattern.finditer(text):
            found.append((p.kind, m.group(0)))
    return found
