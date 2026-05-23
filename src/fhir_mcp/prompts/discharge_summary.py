"""MCP Prompt: discharge-summary template."""

from __future__ import annotations

NAME = "discharge_summary"
DESCRIPTION = "Render a hospital discharge summary for the given patient pseudonym."


def render(patient_pseudonym: str, admission_diagnosis: str = "", encounter_id: str = "") -> str:
    return f"""You are a clinical-documentation assistant. Compose a complete hospital discharge
summary for the patient identified by pseudonym `{patient_pseudonym}`.

Admission diagnosis (from clinician): {admission_diagnosis or "—"}
Encounter id: {encounter_id or "—"}

Use these tools first:
  1. `get_patient_summary({patient_pseudonym!r})`
  2. `get_medications(patient_pseudonym={patient_pseudonym!r}, include_interactions=true)`
  3. `search_observations(patient_pseudonym={patient_pseudonym!r}, trend_window_days=30)`

Produce the discharge summary with these sections:
  - Admission diagnosis
  - Hospital course (chronological)
  - Discharge diagnosis
  - Discharge medications (every change vs admission noted)
  - Pending results / follow-up labs
  - Follow-up appointments
  - Patient instructions (medication adherence, red-flag symptoms)

Constraints: cite labs/vitals with dates; do not invent data; flag any drug-drug interaction warnings
prominently in the discharge medications section.
"""
