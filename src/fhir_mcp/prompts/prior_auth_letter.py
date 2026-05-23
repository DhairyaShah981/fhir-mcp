"""MCP Prompt: prior-authorization letter template."""

from __future__ import annotations

NAME = "prior_auth_letter"
DESCRIPTION = "Render a prior-authorization letter for a specific medication or procedure."


def render(
    patient_pseudonym: str,
    requested_intervention: str,
    payer: str = "",
    diagnosis: str = "",
) -> str:
    return f"""You are a clinical-documentation assistant. Compose a prior-authorization letter for
the patient pseudonym `{patient_pseudonym}` requesting **{requested_intervention or "the named intervention"}**.

Payer: {payer or "—"}
Working diagnosis: {diagnosis or "—"}

Use these tools first:
  1. `get_patient_summary({patient_pseudonym!r})`
  2. `search_conditions(patient_pseudonym={patient_pseudonym!r})`
  3. `search_observations(patient_pseudonym={patient_pseudonym!r}, trend_window_days=365)`

Structure the letter as:
  1. Patient identification (use pseudonym only)
  2. Clinical history relevant to the requested intervention
  3. Failed/trialed alternatives (medications, dosages, durations)
  4. Objective evidence supporting medical necessity (cite labs, imaging, dates)
  5. Guideline citation supporting the request (specify society + year)
  6. Specific ask: dose, frequency, duration, expected outcome metric

Constraints: cite every objective claim with a date; do NOT fabricate prior trials or guideline citations.
If the chart lacks evidence for a section, write "Not documented" rather than inventing content.
"""
