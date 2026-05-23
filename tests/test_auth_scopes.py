"""Unit tests for SMART scope parsing."""

from __future__ import annotations

from fhir_mcp.auth.scopes import REID_SCOPE, ScopeSet, parse_scopes


def test_parse_simple_scopes() -> None:
    s = parse_scopes("patient/Observation.read patient/Condition.read")
    assert s.allows("patient", "Observation", "r")
    assert s.allows("patient", "Condition", "r")
    assert not s.allows("patient", "Observation", "u")


def test_wildcard_resource_grants_everything() -> None:
    s = parse_scopes("patient/*.read")
    assert s.allows("patient", "Observation", "r")
    assert s.allows("patient", "Anything", "r")
    assert not s.allows("system", "Observation", "r")


def test_all_rights_letter_grants_crud() -> None:
    s = parse_scopes("patient/Condition.a")
    for right in "crudsa":
        assert s.allows("patient", "Condition", right)


def test_reid_custom_scope() -> None:
    s = parse_scopes("patient/*.read fhir-mcp/reid")
    assert s.can_reidentify()
    assert s.has_raw(REID_SCOPE)


def test_no_reid_without_scope() -> None:
    s = parse_scopes("patient/*.read")
    assert not s.can_reidentify()


def test_malformed_scopes_ignored() -> None:
    s = parse_scopes("garbage patient/Observation.read")
    assert s.allows("patient", "Observation", "r")


def test_empty_string_safe() -> None:
    s = parse_scopes("")
    assert isinstance(s, ScopeSet)
    assert not s.can_reidentify()
