"""De-id leakage tests.

For every golden bundle we (1) extract the known PHI tokens from the source,
(2) run ``deidentify_resource`` on every entry, (3) scan the output for any of
those tokens. Zero leaks is the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhir_mcp.deid.pipeline import deidentify_resource, scan_for_phi_leaks


def _collect_phi_from_bundle(bundle: dict) -> list[str]:
    tokens: set[str] = set()
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") not in {"Patient", "Practitioner", "RelatedPerson"}:
            continue
        for n in r.get("name", []) or []:
            if n.get("family"):
                tokens.add(n["family"])
            for g in n.get("given", []) or []:
                tokens.add(g)
            if n.get("text"):
                tokens.add(n["text"])
        for ident in r.get("identifier", []) or []:
            if ident.get("value"):
                tokens.add(ident["value"])
        for tel in r.get("telecom", []) or []:
            if tel.get("value"):
                tokens.add(tel["value"])
        for addr in r.get("address", []) or []:
            for line in addr.get("line", []) or []:
                tokens.add(line)
            if addr.get("city"):
                tokens.add(addr["city"])
            if addr.get("postalCode"):
                tokens.add(addr["postalCode"])
        if r.get("birthDate"):
            tokens.add(r["birthDate"])
    return sorted(tokens)


@pytest.mark.eval
async def test_no_phi_leak_in_any_golden_bundle(golden_dir: Path) -> None:
    bundle_files = sorted(golden_dir.glob("*.json"))
    assert bundle_files, "no golden bundles present"
    for path in bundle_files:
        bundle = json.loads(path.read_text())
        known_phi = _collect_phi_from_bundle(bundle)
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            deid = await deidentify_resource(r)
            leaks = scan_for_phi_leaks(deid, known_phi)
            assert leaks == [], (
                f"{path.name} :: {r.get('resourceType')}/{r.get('id')} leaked: {leaks}"
            )
