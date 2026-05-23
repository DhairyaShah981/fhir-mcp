"""Terminology service — LOINC / SNOMED / RxNorm code resolution.

Strategy: offline lookup table first (deterministic for evals), then live
``tx.fhir.org`` as fallback. The offline table covers every code used in
the golden Synthea bundles so the eval suite never depends on network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

from .config import get_settings

log = structlog.get_logger(__name__)

CodeSystem = Literal["loinc", "snomed", "rxnorm", "icd10"]

# Canonical URIs (as used in FHIR coding.system).
_SYSTEM_URIS: dict[CodeSystem, str] = {
    "loinc": "http://loinc.org",
    "snomed": "http://snomed.info/sct",
    "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "icd10": "http://hl7.org/fhir/sid/icd-10-cm",
}

# Reverse: URI → short name.
_URI_TO_SYSTEM: dict[str, CodeSystem] = {v: k for k, v in _SYSTEM_URIS.items()}


@dataclass(frozen=True)
class CodeLookup:
    code: str
    system: CodeSystem
    display: str
    alternatives: tuple[str, ...] = ()  # alternative display names


# Offline, deterministic table.
# Every code that appears in evals/golden/*.json or in the CDS Hooks mock
# rules must be represented here so validate_code passes without network.
_OFFLINE_TABLE: dict[tuple[CodeSystem, str], CodeLookup] = {}


def _add(*entries: CodeLookup) -> None:
    for e in entries:
        _OFFLINE_TABLE[(e.system, e.code)] = e


_add(
    # --- LOINC -------------------------------------------------------------
    CodeLookup("4548-4", "loinc", "Hemoglobin A1c/Hemoglobin.total in Blood",
               ("HbA1c", "Glycohemoglobin")),
    CodeLookup("85354-9", "loinc", "Blood pressure panel with all children optional"),
    CodeLookup("8480-6", "loinc", "Systolic blood pressure"),
    CodeLookup("8462-4", "loinc", "Diastolic blood pressure"),
    CodeLookup("13457-7", "loinc", "Cholesterol in LDL [Mass/volume] in Serum or Plasma"),
    CodeLookup("2089-1", "loinc", "Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay"),
    CodeLookup("2160-0", "loinc", "Creatinine [Mass/volume] in Serum or Plasma"),
    CodeLookup("718-7", "loinc", "Hemoglobin [Mass/volume] in Blood"),
    CodeLookup("33747-0", "loinc", "General appearance of Patient"),
    # --- SNOMED ------------------------------------------------------------
    CodeLookup("44054006", "snomed", "Diabetes mellitus type 2",
               ("Type 2 diabetes mellitus", "T2DM")),
    CodeLookup("59621000", "snomed", "Essential hypertension"),
    CodeLookup("55822004", "snomed", "Hyperlipidemia"),
    CodeLookup("91936005", "snomed", "Allergy to penicillin"),
    CodeLookup("84114007", "snomed", "Heart failure"),
    CodeLookup("42343007", "snomed", "Congestive heart failure",
               ("CHF",)),
    CodeLookup("77386006", "snomed", "Pregnancy"),
    CodeLookup("44054007", "snomed", "Atrial fibrillation"),
    CodeLookup("90460009", "snomed", "Asthma"),
    # --- RxNorm ------------------------------------------------------------
    CodeLookup("860975", "rxnorm", "Metformin hydrochloride 1000 MG Oral Tablet",
               ("Metformin",)),
    CodeLookup("314076", "rxnorm", "Lisinopril 10 MG Oral Tablet", ("Lisinopril",)),
    CodeLookup("617314", "rxnorm", "Atorvastatin 40 MG Oral Tablet", ("Atorvastatin",)),
    CodeLookup("855332", "rxnorm", "Warfarin Sodium 5 MG Oral Tablet", ("Warfarin", "Coumadin")),
    CodeLookup("243670", "rxnorm", "Aspirin 81 MG Oral Tablet", ("Aspirin", "ASA")),
    CodeLookup("197517", "rxnorm", "Ibuprofen 600 MG Oral Tablet", ("Ibuprofen",)),
    CodeLookup("1043400", "rxnorm", "Apixaban 5 MG Oral Tablet", ("Apixaban", "Eliquis")),
    CodeLookup("197378", "rxnorm", "Furosemide 40 MG Oral Tablet", ("Furosemide", "Lasix")),
    CodeLookup("866924", "rxnorm", "Hydrochlorothiazide 25 MG Oral Tablet", ("HCTZ",)),
    # --- ICD-10 ------------------------------------------------------------
    CodeLookup("E11.9", "icd10", "Type 2 diabetes mellitus without complications"),
    CodeLookup("I10", "icd10", "Essential (primary) hypertension"),
    CodeLookup("I50.9", "icd10", "Heart failure, unspecified"),
    CodeLookup("Z34.90", "icd10", "Encounter for supervision of normal pregnancy, unspecified"),
)


def normalize_system(system_or_uri: str) -> CodeSystem | None:
    """Accept either a short name (``loinc``) or canonical URI."""
    if system_or_uri in _SYSTEM_URIS:
        return system_or_uri  # type: ignore[return-value]
    return _URI_TO_SYSTEM.get(system_or_uri)


def system_uri(system: CodeSystem) -> str:
    return _SYSTEM_URIS[system]


def offline_lookup(system: CodeSystem, code: str) -> CodeLookup | None:
    return _OFFLINE_TABLE.get((system, code))


async def live_lookup(system: CodeSystem, code: str) -> CodeLookup | None:
    """Query tx.fhir.org via the ``$lookup`` operation.

    Returns ``None`` on any error so callers can degrade gracefully.
    """
    settings = get_settings()
    params = {"system": system_uri(system), "code": code}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.terminology_url.rstrip('/')}/CodeSystem/$lookup",
                params=params,
                headers={"Accept": "application/fhir+json"},
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("terminology_live_lookup_failed", system=system, code=code, error=str(exc))
        return None

    display = ""
    for param in data.get("parameter", []) or []:
        if param.get("name") == "display":
            display = param.get("valueString", "")
            break
    if not display:
        return None
    return CodeLookup(code=code, system=system, display=display)


async def lookup(system_or_uri: str, code: str, *, allow_live: bool = True) -> CodeLookup | None:
    """Resolve a code. Offline first, optional live fallback."""
    system = normalize_system(system_or_uri)
    if system is None:
        return None
    hit = offline_lookup(system, code)
    if hit is not None:
        return hit
    if allow_live:
        return await live_lookup(system, code)
    return None
