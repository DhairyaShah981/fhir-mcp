"""Structural FHIR de-identification.

We walk known PHI-bearing paths per FHIR resource type and replace each value
with a pseudonym from the vault. Free-text fields run through the regex
recognizers. Pseudonyms are stable per deployment (same input → same output),
so the LLM can reason consistently across calls.

The goal is *zero PHI in egress*. The leakage test suite
(``evals/test_deid.py``) re-scans every tool's output for known PHI tokens and
fails CI if any escapes.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import structlog

from .recognizers import find_phi_spans
from .vault import get_vault

log = structlog.get_logger(__name__)

# Paths we walk per resource type. Each entry is a JSONPath-ish dotted spec;
# we expand into list elements with ``[]``.
_PHI_PATHS: dict[str, list[tuple[str, str]]] = {
    "Patient": [
        ("name[].text", "name"),
        ("name[].given[]", "given"),
        ("name[].family", "family"),
        ("name[].prefix[]", "name"),
        ("name[].suffix[]", "name"),
        ("identifier[].value", "identifier"),
        ("telecom[].value", "phone"),  # both phone + email collapse here
        ("address[].text", "address"),
        ("address[].line[]", "address"),
        ("address[].city", "city"),
        ("address[].postalCode", "postal"),
        ("birthDate", "dob"),
    ],
    "Practitioner": [
        ("name[].text", "name"),
        ("name[].given[]", "given"),
        ("name[].family", "family"),
        ("identifier[].value", "identifier"),
        ("telecom[].value", "phone"),
    ],
    "RelatedPerson": [
        ("name[].text", "name"),
        ("name[].given[]", "given"),
        ("name[].family", "family"),
        ("telecom[].value", "phone"),
    ],
}

# Reference rewriting: any ``Patient/<id>`` → ``Patient/<pseudonym>``.
_REF_PATTERN = re.compile(r"^(Patient|Practitioner|RelatedPerson)/([A-Za-z0-9\-.]+)$")

# Free-text fields scanned per resource type.
_FREETEXT_PATHS: list[str] = [
    "text.div",
    "note[].text",
    "comment",
    "valueString",
    "content[].attachment.title",
]


async def deidentify_resource(resource: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied, de-identified resource. Idempotent."""
    if not resource:
        return resource
    out = copy.deepcopy(resource)
    rt = out.get("resourceType")
    vault = get_vault()

    # 1. Structural PHI replacement.
    if isinstance(rt, str):
        for spec, kind in _PHI_PATHS.get(rt, []):
            await _walk_and_replace(out, spec, kind)

    # 2. Pseudonymize the resource id when it identifies a subject.
    if rt in {"Patient", "Practitioner", "RelatedPerson"} and "id" in out:
        out["id"] = await vault.pseudonymize("patient_ref" if rt == "Patient" else "identifier", str(out["id"]))

    # 3. Rewrite references to subjects.
    await _rewrite_references(out)

    # 4. Sweep free-text fields with regex recognizers.
    for spec in _FREETEXT_PATHS:
        await _walk_freetext(out, spec)

    return out


async def deidentify_bundle(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [await deidentify_resource(r) for r in resources]


# --- internals --------------------------------------------------------------


async def _walk_and_replace(node: Any, spec: str, kind: str) -> None:
    parts = spec.split(".")
    await _walk(node, parts, kind, replace=True)


async def _walk_freetext(node: Any, spec: str) -> None:
    parts = spec.split(".")
    await _walk(node, parts, kind="freetext", replace=False, freetext=True)


async def _walk(node: Any, parts: list[str], kind: str, *, replace: bool, freetext: bool = False) -> None:
    if not parts or node is None:
        return
    head, *rest = parts
    if head.endswith("[]"):
        head = head[:-2]
        children = node.get(head) if isinstance(node, dict) else None
        if not isinstance(children, list):
            return
        for i, child in enumerate(children):
            if rest:
                await _walk(child, rest, kind, replace=replace, freetext=freetext)
            else:
                # Terminal in a list — replace primitive value in place.
                if isinstance(child, str):
                    children[i] = await _maybe_replace(child, kind, freetext)
        return
    if not isinstance(node, dict):
        return
    if rest:
        child = node.get(head)
        if isinstance(child, (dict, list)):
            if isinstance(child, list):
                for item in child:
                    await _walk(item, rest, kind, replace=replace, freetext=freetext)
            else:
                await _walk(child, rest, kind, replace=replace, freetext=freetext)
        return
    # Terminal scalar.
    value = node.get(head)
    if isinstance(value, str):
        node[head] = await _maybe_replace(value, kind, freetext)


async def _maybe_replace(value: str, kind: str, freetext: bool) -> str:
    if not value:
        return value
    vault = get_vault()
    if freetext:
        spans = find_phi_spans(value)
        out = value
        for phi_kind, span in spans:
            ps = await vault.pseudonymize(phi_kind, span)
            out = out.replace(span, ps)
        return out
    return await vault.pseudonymize(kind, value)


async def _rewrite_references(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k == "reference" and isinstance(v, str):
                m = _REF_PATTERN.match(v)
                if m:
                    rt, rid = m.group(1), m.group(2)
                    vault = get_vault()
                    new_id = await vault.pseudonymize(
                        "patient_ref" if rt == "Patient" else "identifier", rid
                    )
                    node[k] = f"{rt}/{new_id}"
            else:
                await _rewrite_references(v)
    elif isinstance(node, list):
        for item in node:
            await _rewrite_references(item)


# --- leakage detection ------------------------------------------------------


def scan_for_phi_leaks(payload: Any, known_phi_tokens: list[str]) -> list[str]:
    """Return any known PHI tokens that still appear in the payload.

    Two-pass: exact lowercase substring (catches most), and a defensive
    whitespace-normalized pass (catches `Jane  Doe` vs `Jane Doe`).
    """
    blob = _stringify(payload).lower()
    blob_norm = " ".join(blob.split())
    leaked: list[str] = []
    for token in known_phi_tokens:
        if not token:
            continue
        t = token.lower()
        if t in blob or t in blob_norm:
            leaked.append(token)
    return leaked


def collect_known_phi_from_bundle(bundle: dict[str, Any]) -> list[str]:
    """Extract every PHI-shaped token from a Synthea-style bundle for leak scans.

    Walks the same paths ``deidentify_resource`` operates on, **across every
    resource type** (including free-text ``note`` / ``comment`` / DocumentReference
    content), so a leak from any non-Patient resource is still caught.

    Critically, we do **not** sweep ``code.*`` (terminology displays like
    "Congestive heart failure" are clinical concepts, not PHI).
    """
    tokens: set[str] = set()
    entries = bundle.get("entry", []) or []
    for entry in entries:
        if isinstance(entry, dict):
            _collect_phi(entry.get("resource"), tokens)
        else:
            _collect_phi(entry, tokens)
    # Skip empty + ultra-short tokens — too noisy to scan reliably.
    return sorted(t for t in tokens if t and len(t) > 2)


# Per-resource path → kind. Mirrors _PHI_PATHS in this module but only what we
# care about for leak detection (every value at these paths *was* PHI before
# de-id and must not reappear in the output).
_PHI_PATHS_PER_RESOURCE: dict[str, list[str]] = {
    "Patient": [
        "name[].text", "name[].given[]", "name[].family",
        "name[].prefix[]", "name[].suffix[]",
        "identifier[].value", "telecom[].value",
        "address[].text", "address[].line[]", "address[].city",
        "address[].postalCode", "birthDate",
    ],
    "Practitioner": [
        "name[].text", "name[].given[]", "name[].family",
        "identifier[].value", "telecom[].value",
    ],
    "RelatedPerson": [
        "name[].text", "name[].given[]", "name[].family", "telecom[].value",
    ],
}

# Free-text paths that exist on many resource types — sweep regardless of rt.
_FREETEXT_LEAK_PATHS = [
    "note[].text", "comment", "valueString",
    "content[].attachment.title",
]


def _collect_phi(node: Any, out: set[str]) -> None:
    if not isinstance(node, dict):
        return
    rt = node.get("resourceType")
    if isinstance(rt, str):
        for spec in _PHI_PATHS_PER_RESOURCE.get(rt, []):
            _walk_collect(node, spec.split("."), out)
    for spec in _FREETEXT_LEAK_PATHS:
        _walk_collect(node, spec.split("."), out)


def _walk_collect(node: Any, parts: list[str], out: set[str]) -> None:
    if not parts or node is None:
        return
    head, *rest = parts
    if head.endswith("[]"):
        head = head[:-2]
        children = node.get(head) if isinstance(node, dict) else None
        if not isinstance(children, list):
            return
        for child in children:
            if rest:
                _walk_collect(child, rest, out)
            elif isinstance(child, str):
                out.add(child)
        return
    if not isinstance(node, dict):
        return
    if rest:
        _walk_collect(node.get(head), rest, out)
        return
    value = node.get(head)
    if isinstance(value, str):
        out.add(value)


def _stringify(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return " ".join(_stringify(v) for v in payload.values())
    if isinstance(payload, list):
        return " ".join(_stringify(v) for v in payload)
    return str(payload)
