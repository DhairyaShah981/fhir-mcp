"""Eval suite: terminology code validation."""

from __future__ import annotations

import pytest

from fhir_mcp.tools.validate_code import ValidateCodeInput, validate_code

# (system, code, expected display fragment) — all from the offline table.
CASES = [
    ("loinc", "4548-4", "hemoglobin a1c"),
    ("loinc", "85354-9", "blood pressure"),
    ("loinc", "8480-6", "systolic"),
    ("loinc", "8462-4", "diastolic"),
    ("loinc", "13457-7", "ldl"),
    ("loinc", "2160-0", "creatinine"),
    ("loinc", "718-7", "hemoglobin"),
    ("snomed", "44054006", "diabetes mellitus type 2"),
    ("snomed", "59621000", "essential hypertension"),
    ("snomed", "55822004", "hyperlipidemia"),
    ("snomed", "42343007", "congestive heart failure"),
    ("snomed", "44054007", "atrial fibrillation"),
    ("snomed", "77386006", "pregnancy"),
    ("snomed", "91936005", "allergy to penicillin"),
    ("snomed", "90460009", "asthma"),
    ("rxnorm", "860975", "metformin"),
    ("rxnorm", "314076", "lisinopril"),
    ("rxnorm", "617314", "atorvastatin"),
    ("rxnorm", "855332", "warfarin"),
    ("rxnorm", "243670", "aspirin"),
    ("rxnorm", "1043400", "apixaban"),
    ("rxnorm", "866924", "hydrochlorothiazide"),
    ("rxnorm", "197378", "furosemide"),
    ("rxnorm", "197517", "ibuprofen"),
    ("icd10", "E11.9", "type 2 diabetes mellitus"),
    ("icd10", "I10", "essential"),
    ("icd10", "I50.9", "heart failure"),
    ("icd10", "Z34.90", "pregnancy"),
]


@pytest.mark.eval
@pytest.mark.parametrize("system,code,fragment", CASES)
async def test_validate_known_codes(system: str, code: str, fragment: str) -> None:
    result = await validate_code(ValidateCodeInput(system=system, code=code))
    assert result.valid, f"{system} {code} did not resolve"
    assert result.display is not None
    assert fragment.lower() in result.display.lower(), (
        f"{system} {code}: expected '{fragment}' in '{result.display}'"
    )


@pytest.mark.eval
async def test_validate_uri_form_accepted() -> None:
    result = await validate_code(
        ValidateCodeInput(system="http://snomed.info/sct", code="44054006")
    )
    assert result.valid
    assert result.system == "snomed"


@pytest.mark.eval
async def test_validate_unknown_code_returns_invalid() -> None:
    result = await validate_code(ValidateCodeInput(system="loinc", code="0000-0"))
    assert result.valid is False
    assert result.display is None


@pytest.mark.eval
async def test_validate_unknown_system_returns_invalid() -> None:
    result = await validate_code(ValidateCodeInput(system="not-a-system", code="123"))
    assert result.valid is False
