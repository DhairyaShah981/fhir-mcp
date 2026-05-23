"""Deterministic offline CDS Hooks service.

Implements a useful subset of CDS Hooks: a small set of evidence-based rules
that fire on realistic patient context. The mock is the **default** so evals
are reproducible — switching to a live sandbox is opt-in.

Each rule returns a list of CDS Hooks ``card`` objects (per the CDS Hooks
1.0 specification: https://cds-hooks.org/specification/current/).
"""

from __future__ import annotations

from typing import Any

# Hook id → human description (exposed via discovery).
MOCK_HOOKS: dict[str, dict[str, str]] = {
    "patient-view": {
        "title": "Patient view checks",
        "description": "Background checks fired when a clinician opens a patient chart.",
    },
    "medication-prescribe": {
        "title": "Drug-drug + drug-allergy interaction checks",
        "description": "Fires when a medication is being prescribed. Returns interactions and allergy warnings.",
    },
    "order-select": {
        "title": "Order appropriateness checks",
        "description": "Statin appropriateness, contrast safety, etc.",
    },
}


# Drug-drug interactions. Pairs of RxNorm codes (or display synonyms) →
# {severity, message}. Keys are sorted tuples so direction doesn't matter.
_DDI: dict[tuple[str, str], dict[str, str]] = {
    ("aspirin", "warfarin"): {
        "severity": "warning",
        "summary": "Bleeding risk: aspirin + warfarin",
        "detail": "Concurrent aspirin and warfarin substantially increases bleeding risk. "
                  "Confirm indication for dual antithrombotic therapy and consider gastric protection.",
    },
    ("apixaban", "aspirin"): {
        "severity": "warning",
        "summary": "Bleeding risk: apixaban + aspirin",
        "detail": "Combining a DOAC with aspirin increases bleeding risk. Reassess need for both agents.",
    },
    ("ibuprofen", "warfarin"): {
        "severity": "warning",
        "summary": "Bleeding risk: NSAIDs + warfarin",
        "detail": "NSAIDs potentiate the bleeding risk of warfarin via platelet inhibition and gastric irritation.",
    },
    ("hydrochlorothiazide", "lisinopril"): {
        "severity": "info",
        "summary": "ACE-I + thiazide — monitor potassium and renal function",
        "detail": "This is a commonly-used combination but can precipitate AKI in volume depletion; monitor labs.",
    },
}

# Drug-allergy: medication RxNorm/display → allergen SNOMED/display.
_DRUG_ALLERGY: dict[tuple[str, str], dict[str, str]] = {
    ("amoxicillin", "penicillin"): {
        "severity": "critical",
        "summary": "Documented penicillin allergy",
        "detail": "Patient has a documented allergy to penicillin. Amoxicillin is a penicillin-class antibiotic — avoid.",
    },
    ("ampicillin", "penicillin"): {
        "severity": "critical",
        "summary": "Documented penicillin allergy",
        "detail": "Patient has a documented allergy to penicillin. Ampicillin is contraindicated.",
    },
}


def _normalize(text: str) -> str:
    return (text or "").lower()


def _med_displays(medications: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for m in medications:
        mc = m.get("medicationCodeableConcept") or m.get("medication") or {}
        if isinstance(mc, dict):
            if mc.get("text"):
                out.append(_normalize(mc["text"]))
            for c in mc.get("coding", []) or []:
                if c.get("display"):
                    out.append(_normalize(c["display"]))
                if c.get("code"):
                    out.append(_normalize(c["code"]))
    return out


def _allergy_displays(allergies: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for a in allergies:
        code = a.get("code") or {}
        if isinstance(code, dict):
            if code.get("text"):
                out.append(_normalize(code["text"]))
            for c in code.get("coding", []) or []:
                if c.get("display"):
                    out.append(_normalize(c["display"]))
    return out


def _match_token(target: str, haystack: list[str]) -> bool:
    return any(target in candidate for candidate in haystack)


def _card(severity: str, summary: str, detail: str, source: str = "fhir-mcp/mock") -> dict[str, Any]:
    return {
        "summary": summary,
        "detail": detail,
        "indicator": severity,  # info | warning | critical
        "source": {"label": source},
    }


def _drug_drug_cards(med_displays: list[str], proposed_displays: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    haystack = med_displays + proposed_displays
    for (a, b), rule in _DDI.items():
        if _match_token(a, haystack) and _match_token(b, haystack):
            cards.append(_card(rule["severity"], rule["summary"], rule["detail"]))
    return cards


def _drug_allergy_cards(
    proposed_displays: list[str], allergy_displays: list[str]
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for (drug, allergen), rule in _DRUG_ALLERGY.items():
        if _match_token(drug, proposed_displays) and _match_token(allergen, allergy_displays):
            cards.append(_card(rule["severity"], rule["summary"], rule["detail"]))
    return cards


def _patient_view_cards(prefetch: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    obs_list = _coerce_resources(prefetch.get("observations"))
    for o in obs_list:
        code = o.get("code") or {}
        text_parts: list[str] = []
        if isinstance(code, dict):
            text_parts.append(_normalize(code.get("text", "")))
            for c in code.get("coding", []) or []:
                text_parts.append(_normalize(c.get("display", "")))
        v = o.get("valueQuantity") or {}
        if (
            v.get("value") is not None
            and any("hba1c" in t or "hemoglobin a1c" in t for t in text_parts)
            and float(v["value"]) >= 9.0
        ):
            cards.append(
                _card(
                    "warning",
                    f"Poor glycemic control (HbA1c {v['value']}%)",
                    "HbA1c ≥ 9.0% suggests poorly controlled diabetes — consider intensifying therapy.",
                )
            )
    return cards


def _coerce_resources(value: Any) -> list[dict[str, Any]]:
    """Accept either a list of resources or a FHIR Bundle dict; return the resources."""
    if value is None:
        return []
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        entries = value.get("entry") or []
        return [e.get("resource", {}) for e in entries if isinstance(e, dict)]
    return []


def mock_invoke(hook: str, context: dict[str, Any], prefetch: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute the mock hook and return a CDS Hooks response (``{"cards": [...]}``)."""
    prefetch = prefetch or {}
    medications = _coerce_resources(prefetch.get("medications"))
    allergies = _coerce_resources(prefetch.get("allergies"))

    med_displays = _med_displays(medications)
    allergy_displays = _allergy_displays(allergies)

    # The "proposed" medication — either context.draftOrders or context.medications.
    proposed: list[dict[str, Any]] = []
    proposed.extend(_coerce_resources(context.get("draftOrders")))
    proposed.extend(_coerce_resources(context.get("medications")))
    proposed_displays = _med_displays(proposed)

    cards: list[dict[str, Any]] = []
    if hook == "medication-prescribe":
        cards.extend(_drug_drug_cards(med_displays, proposed_displays))
        cards.extend(_drug_allergy_cards(proposed_displays, allergy_displays))
    elif hook == "patient-view":
        cards.extend(_patient_view_cards(prefetch))
        # Always also run DDI on existing meds — useful as a safety net.
        cards.extend(_drug_drug_cards(med_displays, []))
    elif hook == "order-select":
        # Minimal statin-prescribe heuristic.
        if _match_token("statin", proposed_displays) or _match_token("atorvastatin", proposed_displays):
            cards.append(
                _card(
                    "info",
                    "Statin therapy initiated",
                    "Confirm baseline LFTs and lipid panel; review for muscle symptoms at follow-up.",
                )
            )
    # Unknown hooks return an empty cards array — same as live behavior.
    return {"cards": cards}
