"""In-memory FHIR backend backed by frozen Synthea-shaped bundles.

Bundles live in ``evals/golden/*.json`` and are loaded once at startup. Search is
deterministic and zero-network — perfect for evals and demos.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _golden_dir() -> Path:
    """Locate the Synthea golden bundles.

    Dev mode (running from a checkout): use ``evals/golden/`` at the repo root.
    Installed-wheel mode (``uvx fhir-mcp serve``): use the package-bundled copy
    at ``src/fhir_mcp/_bundled_data/golden/`` (placed there by hatch
    ``force-include`` at build time — see ``pyproject.toml``).
    """
    here = Path(__file__).resolve()
    # Repo path is parents[3] only when the source tree is laid out as
    # ``<repo>/src/fhir_mcp/backends/synthea.py``. When installed via uvx /
    # pip the wheel layout is ``<site-packages>/fhir_mcp/backends/synthea.py``
    # — parents[3] then points outside site-packages and won't exist.
    candidates = [
        here.parents[3] / "evals" / "golden",      # dev checkout
        here.parent.parent / "_bundled_data" / "golden",  # wheel
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # logged as warning when empty


class SyntheaBackend:
    name: str = "synthea"

    def __init__(self, bundle_paths: list[Path] | None = None) -> None:
        self._by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._by_id: dict[tuple[str, str], dict[str, Any]] = {}
        self._patient_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)

        paths = bundle_paths or sorted(_golden_dir().glob("*.json"))
        if not paths:
            log.warning("synthea_no_bundles", searched=str(_golden_dir()))
        for path in paths:
            self._load_bundle(path)
        log.info(
            "synthea_loaded",
            bundles=len(paths),
            resources=sum(len(v) for v in self._by_type.values()),
            types=sorted(self._by_type.keys()),
        )

    def _load_bundle(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            log.error("synthea_bundle_load_failed", path=str(path), error=str(exc))
            return
        if data.get("resourceType") != "Bundle":
            log.warning("synthea_skipped_non_bundle", path=str(path))
            return
        for entry in data.get("entry", []):
            resource = entry.get("resource")
            if not resource:
                continue
            rt = resource.get("resourceType")
            rid = resource.get("id")
            if not rt or not rid:
                continue
            self._by_type[rt].append(resource)
            self._by_id[(rt, rid)] = resource

            # Index by patient subject reference for fast $everything.
            subj = resource.get("subject") or resource.get("patient")
            ref = subj.get("reference") if isinstance(subj, dict) else None
            if ref and ref.startswith("Patient/"):
                self._patient_refs[ref.removeprefix("Patient/")].append(resource)
            elif rt == "Patient":
                self._patient_refs[rid].append(resource)

    # ---- FhirBackend protocol ------------------------------------------------

    async def search(self, resource_type: str, params: dict[str, str | int]) -> list[dict]:
        candidates = list(self._by_type.get(resource_type, []))
        if not candidates:
            return []

        # Lightweight FHIR-search emulation: enough for our tools.
        matched: list[dict] = []
        name_q = str(params.get("name", "")).lower()
        family_q = str(params.get("family", "")).lower()
        given_q = str(params.get("given", "")).lower()
        identifier_q = str(params.get("identifier", "")).lower()
        birthdate_q = str(params.get("birthdate", ""))
        gender_q = str(params.get("gender", "")).lower()
        patient_q = str(params.get("patient", "") or params.get("subject", "")).removeprefix(
            "Patient/"
        )
        clinical_status_q = str(params.get("clinical-status", "")).lower()
        code_q = str(params.get("code", "")).lower()
        date_ge = str(params.get("date_ge", ""))  # custom convenience
        date_le = str(params.get("date_le", ""))
        limit = int(params.get("_count", 50))

        for r in candidates:
            if resource_type == "Patient":
                if name_q and not _patient_name_matches(r, name_q):
                    continue
                if family_q and not _patient_family_matches(r, family_q):
                    continue
                if given_q and not _patient_given_matches(r, given_q):
                    continue
                if identifier_q and not _patient_identifier_matches(r, identifier_q):
                    continue
                if birthdate_q and r.get("birthDate", "") != birthdate_q:
                    continue
                if gender_q and r.get("gender", "").lower() != gender_q:
                    continue
            else:
                subj = r.get("subject") or r.get("patient") or {}
                subj_ref = subj.get("reference", "") if isinstance(subj, dict) else ""
                if patient_q and subj_ref != f"Patient/{patient_q}":
                    continue
                if clinical_status_q:
                    cs = r.get("clinicalStatus", {})
                    cs_code = (
                        cs.get("coding", [{}])[0].get("code", "").lower()
                        if isinstance(cs, dict)
                        else ""
                    )
                    if cs_code != clinical_status_q:
                        continue
                if code_q and not _resource_has_code(r, code_q):
                    continue
                if date_ge or date_le:
                    d = _resource_date(r)
                    if d is None:
                        continue
                    if date_ge and d < date_ge:
                        continue
                    if date_le and d > date_le:
                        continue
            matched.append(r)
            if len(matched) >= limit:
                break
        return matched

    async def read(self, resource_type: str, resource_id: str) -> dict | None:
        return self._by_id.get((resource_type, resource_id))

    async def everything(self, patient_id: str) -> list[dict]:
        return list(self._patient_refs.get(patient_id, []))


# --- helpers ----------------------------------------------------------------


def _patient_name_matches(resource: dict, query: str) -> bool:
    for n in resource.get("name", []) or []:
        if query in (n.get("text", "") or "").lower():
            return True
        if query in (n.get("family", "") or "").lower():
            return True
        for g in n.get("given", []) or []:
            if query in g.lower():
                return True
    return False


def _patient_family_matches(resource: dict, query: str) -> bool:
    return any(query in (n.get("family", "") or "").lower() for n in resource.get("name", []) or [])


def _patient_given_matches(resource: dict, query: str) -> bool:
    for n in resource.get("name", []) or []:
        if any(query in g.lower() for g in n.get("given", []) or []):
            return True
    return False


def _patient_identifier_matches(resource: dict, query: str) -> bool:
    for ident in resource.get("identifier", []) or []:
        if query in (ident.get("value", "") or "").lower():
            return True
    return False


def _resource_has_code(resource: dict, query: str) -> bool:
    code = resource.get("code", {})
    if not isinstance(code, dict):
        return False
    for coding in code.get("coding", []) or []:
        if query in (coding.get("code", "") or "").lower():
            return True
        if query in (coding.get("display", "") or "").lower():
            return True
    return query in (code.get("text", "") or "").lower()


def _resource_date(resource: dict) -> str | None:
    """Pick the first date-like field. ISO-8601 strings sort correctly."""
    for key in ("effectiveDateTime", "issued", "date", "recordedDate", "onsetDateTime"):
        v = resource.get(key)
        if isinstance(v, str):
            return v
    period = resource.get("effectivePeriod")
    if isinstance(period, dict) and isinstance(period.get("start"), str):
        return period["start"]
    return None


