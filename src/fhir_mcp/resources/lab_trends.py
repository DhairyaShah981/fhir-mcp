"""MCP Resource: ``fhir://patient/{pseudonym}/trends`` — lab trend data (JSON)."""

from __future__ import annotations

import json

from ..tools.search_observations import (
    SearchObservationsInput,
    search_observations,
)

URI_TEMPLATE = "fhir://patient/{pseudonym}/trends"

# Default panel: HbA1c, BP, LDL, Creatinine.
_PANEL: list[tuple[str, str]] = [
    ("4548-4", "HbA1c"),
    ("8480-6", "Systolic BP"),
    ("13457-7", "LDL"),
    ("2160-0", "Creatinine"),
]


async def render(pseudonym: str, window_days: int = 365) -> str:
    out: dict[str, dict] = {}
    for loinc, label in _PANEL:
        result = await search_observations(
            SearchObservationsInput(
                patient_pseudonym=pseudonym,
                loinc=loinc,
                trend_window_days=window_days,
            )
        )
        if result.trend is None:
            continue
        out[label] = {
            "code": result.trend.code,
            "count": result.trend.count,
            "first_value": result.trend.first_value,
            "last_value": result.trend.last_value,
            "mean_value": result.trend.mean_value,
            "slope_per_day": result.trend.slope_per_day,
            "direction": result.trend.direction,
        }
    return json.dumps({"pseudonym": pseudonym, "window_days": window_days, "trends": out}, indent=2)
