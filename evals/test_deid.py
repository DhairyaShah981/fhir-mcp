"""De-id leakage tests.

For every golden bundle we (1) extract the known PHI tokens from the source,
(2) run ``deidentify_resource`` on every entry, (3) scan the output for any of
those tokens. Zero leaks is the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhir_mcp.deid.pipeline import (
    collect_known_phi_from_bundle,
    deidentify_resource,
    scan_for_phi_leaks,
)


@pytest.mark.eval
async def test_no_phi_leak_in_any_golden_bundle(golden_dir: Path) -> None:
    """Walks every resource in every bundle; scans every de-id output for every
    known PHI token across all resource types (not just Patient/Practitioner).
    This is the headline 'zero leaks' contract."""
    bundle_files = sorted(golden_dir.glob("*.json"))
    assert bundle_files, "no golden bundles present"
    grand_total_resources = 0
    grand_total_tokens = 0
    for path in bundle_files:
        bundle = json.loads(path.read_text())
        known_phi = collect_known_phi_from_bundle(bundle)
        grand_total_tokens += len(known_phi)
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            grand_total_resources += 1
            deid = await deidentify_resource(r)
            leaks = scan_for_phi_leaks(deid, known_phi)
            assert leaks == [], (
                f"{path.name} :: {r.get('resourceType')}/{r.get('id')} leaked: {leaks}"
            )
    # Sanity floor — if these numbers shrink, the golden corpus regressed.
    assert grand_total_resources >= 40, (
        f"only {grand_total_resources} resources scanned; expected ≥40"
    )
    assert grand_total_tokens >= 30, (
        f"only {grand_total_tokens} PHI tokens extracted; the leak gate is too narrow"
    )
