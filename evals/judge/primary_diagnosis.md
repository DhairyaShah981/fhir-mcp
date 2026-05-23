# Judge prompt — primary diagnosis identification

**Scenario:** the assistant has called `get_patient_summary` for a patient
known to have Type 2 Diabetes Mellitus as their primary, longest-standing
active condition. The structured summary is provided below.

## What we are scoring

Does the structured summary correctly identify **Type 2 Diabetes Mellitus**
(SNOMED 44054006, ICD-10 E11.x, or text variants) as the primary diagnosis,
and surface the supporting evidence (HbA1c values, metformin on the active
medication list)?

## Rubric (0–10)

| Score | Meaning                                                                              |
|------:|---------------------------------------------------------------------------------------|
|    10 | Primary Dx correctly = T2DM. HbA1c trend and metformin both surfaced. Coding correct. |
|     9 | Primary Dx correctly = T2DM. Either HbA1c or metformin surfaced (not both).            |
|     8 | Primary Dx correctly = T2DM. Supporting evidence partially correct.                    |
|     5 | T2DM is somewhere in `conditions[]` but `primary_diagnosis` is wrong or empty.         |
|     2 | T2DM is missing entirely OR a different unrelated condition is named primary.          |
|     0 | The summary is malformed / unparseable / contains raw PHI tokens.                      |

**Pass threshold:** ≥ 8.

## Required output JSON

```json
{
  "score": <0-10>,
  "primary_dx_identified": "<text>",
  "evidence_present": ["hba1c", "metformin"],
  "phi_leak_observed": false,
  "rationale": "<one sentence>"
}
```
