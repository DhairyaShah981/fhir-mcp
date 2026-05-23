"""``search_observations`` — point-in-time + temporal trend queries on FHIR Observations.

When ``trend_window_days`` is set, the tool returns aggregated trend statistics
(count, first, last, mean, slope-per-day) instead of raw observations. This
lets the LLM ask "HbA1c slope over last 12 months" as a first-class query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..audit import audited
from ..deid.pipeline import deidentify_resource
from ..deid.vault import get_vault
from ..fhir_client import get_backend
from ..observability import traced

ValueOp = Literal["gt", "lt", "ge", "le", "eq"]


class SearchObservationsInput(BaseModel):
    patient_pseudonym: str | None = Field(
        default=None,
        description="Optional patient pseudonym to scope the search. Omit for all patients.",
    )
    loinc: str | None = Field(
        default=None, description="LOINC code or fragment to filter by, e.g. 4548-4 for HbA1c."
    )
    text: str | None = Field(default=None, description="Free-text fragment matched against code.text.")
    date_from: str | None = Field(default=None, description="ISO date inclusive lower bound.")
    date_to: str | None = Field(default=None, description="ISO date inclusive upper bound.")
    value_op: ValueOp | None = None
    value: float | None = None
    trend_window_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="If set, return trend stats over the last N days instead of raw observations.",
    )
    limit: int = Field(default=25, ge=1, le=200)


class ObservationRow(BaseModel):
    code: str | None
    display: str | None
    text: str | None
    value: float | None
    unit: str | None
    effective: str | None


class TrendStats(BaseModel):
    code: str | None
    display: str | None
    count: int
    window_days: int
    first_value: float | None
    last_value: float | None
    mean_value: float | None
    slope_per_day: float | None
    direction: Literal["rising", "falling", "stable", "unknown"]


class SearchObservationsOutput(BaseModel):
    mode: Literal["raw", "trend"]
    count: int
    observations: list[ObservationRow] = Field(default_factory=list)
    trend: TrendStats | None = None


def _extract_value(obs: dict[str, Any]) -> tuple[float | None, str | None]:
    v = obs.get("valueQuantity")
    if isinstance(v, dict) and v.get("value") is not None:
        try:
            return float(v["value"]), v.get("unit")
        except (TypeError, ValueError):
            return None, v.get("unit")
    return None, None


def _matches_value(value: float | None, op: ValueOp | None, target: float | None) -> bool:
    if op is None or target is None:
        return True
    if value is None:
        return False
    return {
        "gt": value > target,
        "lt": value < target,
        "ge": value >= target,
        "le": value <= target,
        "eq": value == target,
    }[op]


def _slope_per_day(points: list[tuple[datetime, float]]) -> float | None:
    if len(points) < 2:
        return None
    base = points[0][0]
    xs = [(t - base).total_seconds() / 86400.0 for t, _ in points]
    ys = [v for _, v in points]
    n = len(points)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den


def _direction(slope: float | None) -> Literal["rising", "falling", "stable", "unknown"]:
    if slope is None:
        return "unknown"
    if abs(slope) < 1e-4:
        return "stable"
    return "rising" if slope > 0 else "falling"


@traced("search_observations")
@audited("search_observations")
async def search_observations(payload: SearchObservationsInput) -> SearchObservationsOutput:
    backend = get_backend()
    vault = get_vault()

    params: dict[str, str | int] = {"_count": payload.limit}
    if payload.patient_pseudonym:
        real_id = await vault.lookup(payload.patient_pseudonym) or payload.patient_pseudonym
        params["patient"] = real_id
    if payload.loinc:
        params["code"] = payload.loinc
    if payload.date_from:
        params["date_ge"] = payload.date_from
    if payload.date_to:
        params["date_le"] = payload.date_to

    raw = await backend.search("Observation", params)

    # Free-text filter (post-search to keep backend interfaces simple).
    if payload.text:
        needle = payload.text.lower()
        raw = [
            r
            for r in raw
            if needle in (r.get("code", {}).get("text", "") or "").lower()
            or any(
                needle in (c.get("display", "") or "").lower()
                for c in (r.get("code", {}).get("coding") or [])
            )
        ]

    # Value filter.
    if payload.value_op or payload.value is not None:
        filtered: list[dict[str, Any]] = []
        for r in raw:
            v, _ = _extract_value(r)
            if _matches_value(v, payload.value_op, payload.value):
                filtered.append(r)
        raw = filtered

    if payload.trend_window_days:
        cutoff = datetime.now(UTC) - timedelta(days=payload.trend_window_days)
        in_window: list[tuple[datetime, float, dict[str, Any]]] = []
        for r in raw:
            v, _ = _extract_value(r)
            if v is None:
                continue
            eff = r.get("effectiveDateTime") or r.get("issued")
            if not eff:
                continue
            try:
                ts = datetime.fromisoformat(eff.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            except ValueError:
                continue
            if ts >= cutoff:
                in_window.append((ts, v, r))
        in_window.sort(key=lambda x: x[0])
        if not in_window:
            return SearchObservationsOutput(
                mode="trend",
                count=0,
                trend=TrendStats(
                    code=None, display=None, count=0, window_days=payload.trend_window_days,
                    first_value=None, last_value=None, mean_value=None,
                    slope_per_day=None, direction="unknown",
                ),
            )
        points = [(t, v) for t, v, _ in in_window]
        slope = _slope_per_day(points)
        first_v = points[0][1]
        last_v = points[-1][1]
        mean_v = sum(v for _, v in points) / len(points)
        # Take code metadata from the most recent observation.
        last_resource = in_window[-1][2]
        coding_list = last_resource.get("code", {}).get("coding") or []
        coding = coding_list[0] if coding_list else {}
        return SearchObservationsOutput(
            mode="trend",
            count=len(points),
            trend=TrendStats(
                code=coding.get("code"),
                display=coding.get("display") or last_resource.get("code", {}).get("text"),
                count=len(points),
                window_days=payload.trend_window_days,
                first_value=first_v,
                last_value=last_v,
                mean_value=round(mean_v, 4),
                slope_per_day=slope,
                direction=_direction(slope),
            ),
        )

    # Raw mode — de-identify and project each observation.
    rows: list[ObservationRow] = []
    for r in raw[: payload.limit]:
        deid = await deidentify_resource(r)
        code = deid.get("code", {})
        coding_list = code.get("coding") or []
        coding = coding_list[0] if coding_list else {}
        v, unit = _extract_value(deid)
        rows.append(
            ObservationRow(
                code=coding.get("code"),
                display=coding.get("display"),
                text=code.get("text"),
                value=v,
                unit=unit,
                effective=deid.get("effectiveDateTime"),
            )
        )
    return SearchObservationsOutput(mode="raw", count=len(rows), observations=rows)
