"""MCP Resource: ``fhir://patient/{pseudonym}/summary`` — markdown patient summary."""

from __future__ import annotations

from ..tools.get_patient_summary import GetPatientSummaryInput, get_patient_summary

URI_TEMPLATE = "fhir://patient/{pseudonym}/summary"


async def render(pseudonym: str) -> str:
    summary = await get_patient_summary(GetPatientSummaryInput(patient_pseudonym=pseudonym))
    lines: list[str] = []
    lines.append(f"# Patient summary — `{summary.patient_pseudonym}`")
    lines.append("")
    meta = []
    if summary.gender:
        meta.append(f"**Gender:** {summary.gender}")
    if summary.age_band:
        meta.append(f"**Age band:** {summary.age_band}")
    if meta:
        lines.append("  ·  ".join(meta))
        lines.append("")

    if summary.primary_diagnosis and summary.primary_diagnosis.text:
        pd = summary.primary_diagnosis
        lines.append(f"## Primary diagnosis\n- **{pd.text}** ({pd.system or '?'} {pd.code or '?'})\n")

    if summary.conditions:
        lines.append("## Conditions")
        for c in summary.conditions:
            status = c.clinical_status or "?"
            onset = c.onset or "?"
            lines.append(f"- [{status}] {c.code.text or c.code.display or c.code.code} (onset {onset})")
        lines.append("")

    if summary.active_medications:
        lines.append("## Active medications")
        for m in summary.active_medications:
            lines.append(f"- {m.code.text or m.code.display or m.code.code} — {m.dosage or 'no dosing recorded'}")
        lines.append("")

    if summary.allergies:
        lines.append("## Allergies")
        for a in summary.allergies:
            lines.append(f"- {a.code.text or a.code.display}")
        lines.append("")

    if summary.recent_observations:
        lines.append("## Recent observations")
        for o in summary.recent_observations:
            v = f"{o.value} {o.unit}" if o.value is not None else ""
            lines.append(f"- {o.effective or '?'} — {o.code.text or o.code.display}: {v}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
