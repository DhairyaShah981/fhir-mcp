"""MCP Resource: ``fhir://patient/{pseudonym}/medications`` — markdown med list."""

from __future__ import annotations

from ..tools.get_medications import GetMedicationsInput, get_medications

URI_TEMPLATE = "fhir://patient/{pseudonym}/medications"


async def render(pseudonym: str) -> str:
    result = await get_medications(GetMedicationsInput(patient_pseudonym=pseudonym))
    lines = [f"# Active medications — `{pseudonym}`", ""]
    if not result.medications:
        lines.append("_No active medications on file._")
    for m in result.medications:
        lines.append(f"- **{m.text or m.display}** — {m.dosage or 'no dosing recorded'}")
        if m.rxnorm:
            lines.append(f"  - RxNorm: `{m.rxnorm}` · started {m.started or '?'}")
    if result.interactions:
        lines.append("\n## ⚠️ Interaction flags")
        for c in result.interactions:
            lines.append(f"- **[{c.severity}]** {c.summary}")
            if c.detail:
                lines.append(f"  - {c.detail}")
    return "\n".join(lines).strip() + "\n"
