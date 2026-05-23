"""Unit tests for search_observations — raw + trend modes, value filters."""

from __future__ import annotations

from fhir_mcp.tools.search_observations import (
    SearchObservationsInput,
    search_observations,
)


async def test_raw_mode_returns_observations() -> None:
    out = await search_observations(
        SearchObservationsInput(patient_pseudonym="patient-diabetic-60yo", loinc="4548-4")
    )
    assert out.mode == "raw"
    assert out.count >= 1
    assert all(o.code == "4548-4" for o in out.observations)


async def test_value_filter_gt() -> None:
    out = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            value_op="gt",
            value=8.0,
        )
    )
    assert all((o.value or 0) > 8.0 for o in out.observations)


async def test_value_filter_lt() -> None:
    out = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            value_op="lt",
            value=8.0,
        )
    )
    assert all((o.value or 99) < 8.0 for o in out.observations)


async def test_value_filter_ge_le_eq() -> None:
    for op in ("ge", "le", "eq"):
        out = await search_observations(
            SearchObservationsInput(
                patient_pseudonym="patient-diabetic-60yo",
                loinc="4548-4",
                value_op=op,  # type: ignore[arg-type]
                value=7.8,
            )
        )
        # Should not crash for any op; eq must include the 7.8 reading.
        if op == "eq":
            assert any(o.value == 7.8 for o in out.observations)


async def test_text_filter_postscan() -> None:
    out = await search_observations(
        SearchObservationsInput(patient_pseudonym="patient-diabetic-60yo", text="hba1c")
    )
    assert out.count >= 1


async def test_date_range_filter() -> None:
    out = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            date_from="2026-01-01",
            date_to="2026-12-31",
        )
    )
    assert out.count == 1  # only the 2026-04-12 reading


async def test_trend_mode_falling_slope() -> None:
    out = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            trend_window_days=800,
        )
    )
    assert out.mode == "trend"
    assert out.trend is not None
    assert out.trend.count >= 2
    assert out.trend.direction == "falling"
    assert out.trend.slope_per_day is not None and out.trend.slope_per_day < 0


async def test_trend_mode_empty_window() -> None:
    out = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            trend_window_days=1,  # nothing in the last day
        )
    )
    assert out.mode == "trend"
    assert out.trend is not None
    assert out.trend.count == 0
    assert out.trend.direction == "unknown"


async def test_trend_returns_stable_for_flat_series() -> None:
    from datetime import UTC
    from datetime import datetime as dt

    from fhir_mcp.tools import search_observations as so

    points = [
        (dt(2026, 1, 1, tzinfo=UTC), 5.0),
        (dt(2026, 2, 1, tzinfo=UTC), 5.0),
    ]
    slope = so._slope_per_day(points)
    assert slope == 0.0
    assert so._direction(slope) == "stable"


async def test_search_without_patient_pseudonym_searches_all() -> None:
    out = await search_observations(SearchObservationsInput(loinc="4548-4", limit=10))
    assert out.count >= 1
