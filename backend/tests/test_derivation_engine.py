"""Unit tests for schema-driven attribute derivation, and the guard that keeps a
derivation pattern from backtracking exponentially (ReDoS)."""
from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.auth import DEMO_BROKER_FIRM_ID, DEMO_CLIENT_ID, CurrentUser, get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models.schema_def import EmployeeAttributeSchema
from app.services.derivation_engine import (
    MAX_MATCH_INPUT,
    MAX_PATTERN_LENGTH,
    apply_rule,
    derive,
    pattern_error,
    rule_pattern_error,
)


def _schema(attribute_id: str, rule: dict | None) -> EmployeeAttributeSchema:
    """Build a transient (un-persisted) schema row for tests."""
    return EmployeeAttributeSchema(
        client_id=None,
        attribute_id=attribute_id,
        display_name=attribute_id,
        data_type="string",
        derivation_rule=rule,
    )


def test_regex_extract_with_int_cast() -> None:
    schemas = [
        _schema(
            "grade",
            {
                "op": "regex_extract",
                "source": "category",
                "pattern": r"Grade\s+(\d+)",
                "group": 1,
                "cast": "int",
            },
        )
    ]
    out = derive({"category": "Hay Grade 17 Married"}, schemas)
    assert out == {"grade": 17}


def test_job_category_derives_from_job_grade() -> None:
    # Mirrors the seeded job_category rule: pull the grade code off the roster
    # Job Grade column so code-range rules ("Job category: A1 to A9") can match.
    schemas = [
        _schema(
            "job_category",
            {
                "op": "regex_extract",
                "source": "job_grade",
                "pattern": r"^\s*([A-Za-z0-9]+)",
                "group": 1,
            },
        )
    ]
    assert derive({"job_grade": " A9 "}, schemas) == {"job_category": "A9"}
    assert derive({"job_grade": "E10"}, schemas) == {"job_category": "E10"}
    assert derive({}, schemas) == {}


def test_regex_extract_no_match_returns_empty() -> None:
    schemas = [
        _schema(
            "grade",
            {
                "op": "regex_extract",
                "source": "category",
                "pattern": r"Grade\s+(\d+)",
                "cast": "int",
            },
        )
    ]
    assert derive({"category": "no grade here"}, schemas) == {}


def test_regex_extract_missing_source_returns_empty() -> None:
    schemas = [
        _schema(
            "grade",
            {"op": "regex_extract", "source": "category", "pattern": r"(\d+)"},
        )
    ]
    assert derive({"other_field": "stuff"}, schemas) == {}


def test_regex_extract_bad_regex_is_logged_not_raised(caplog) -> None:
    schemas = [
        _schema(
            "grade",
            {"op": "regex_extract", "source": "category", "pattern": r"["},
        )
    ]
    out = derive({"category": "Hay Grade 17"}, schemas)
    assert out == {}


def test_regex_extract_uncastable_returns_empty() -> None:
    schemas = [
        _schema(
            "grade",
            {
                "op": "regex_extract",
                "source": "category",
                "pattern": r"Grade\s+(\w+)",
                "cast": "int",
            },
        )
    ]
    out = derive({"category": "Grade ABC"}, schemas)
    assert out == {}


def test_regex_case_first_match_wins() -> None:
    schemas = [
        _schema(
            "family_status",
            {
                "op": "regex_case",
                "source": "category",
                "cases": [
                    {"pattern": r"(?i)married.+2\s*child", "value": "M2C"},
                    {"pattern": r"(?i)married", "value": "M"},
                    {"pattern": r"(?i)single", "value": "S"},
                ],
            },
        )
    ]
    out = derive({"category": "17 Married plus 2 child"}, schemas)
    assert out == {"family_status": "M2C"}


def test_regex_case_falls_through_to_default() -> None:
    schemas = [
        _schema(
            "family_status",
            {
                "op": "regex_case",
                "source": "category",
                "cases": [{"pattern": r"(?i)married", "value": "M"}],
                "default": "S",
            },
        )
    ]
    out = derive({"category": "20 Single"}, schemas)
    assert out == {"family_status": "S"}


def test_regex_case_no_match_no_default_returns_empty() -> None:
    schemas = [
        _schema(
            "family_status",
            {
                "op": "regex_case",
                "source": "category",
                "cases": [{"pattern": r"(?i)divorced", "value": "D"}],
            },
        )
    ]
    assert derive({"category": "Single"}, schemas) == {}


def test_passthrough_copies_raw_attribute() -> None:
    schemas = [_schema("pass", {"op": "passthrough", "source": "pass"})]
    assert derive({"pass": "EP"}, schemas) == {"pass": "EP"}


def test_passthrough_empty_source_omitted() -> None:
    schemas = [_schema("pass", {"op": "passthrough", "source": "pass"})]
    assert derive({"pass": ""}, schemas) == {}


def test_schemas_without_derivation_rule_are_skipped() -> None:
    schemas = [_schema("nationality", None)]
    assert derive({"nationality": "SG"}, schemas) == {}


def test_unknown_op_is_skipped() -> None:
    schemas = [_schema("grade", {"op": "alien_op", "source": "category"})]
    assert derive({"category": "stuff"}, schemas) == {}


def test_multiple_schemas_independent() -> None:
    schemas = [
        _schema(
            "grade",
            {
                "op": "regex_extract",
                "source": "category",
                "pattern": r"Grade\s+(\d+)",
                "cast": "int",
            },
        ),
        _schema(
            "family_status",
            {
                "op": "regex_case",
                "source": "category",
                "cases": [{"pattern": r"(?i)married", "value": "M"}],
            },
        ),
        _schema("pass", {"op": "passthrough", "source": "pass"}),
    ]
    out = derive(
        {"category": "Hay Grade 12 Married", "pass": "EP"},
        schemas,
    )
    assert out == {"grade": 12, "family_status": "M", "pass": "EP"}


# ── ReDoS guard ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+", r"(.*)*", r"(\w+)*", r"(a|aa)+", r"((ab)+)+", r"(a?)+",
        r"(\d{2,4})+", r"(?:\s|-)+", r"(a+)(?#comment)+", r"(?:x(a|b)y){2}",
    ],
)
def test_nested_repetition_is_refused(pattern: str) -> None:
    problem = pattern_error(pattern)
    assert problem is not None
    assert "exponential time" in problem


@pytest.mark.parametrize(
    "pattern",
    [
        r"\b(\d{1,2})\b",
        r"^\s*([A-Za-z0-9]+)",
        r"(?i)\bintern(?:s)?\b(?!.*oversea)",
        r"(?i)(?:married|spouse).{0,40}(?:2|two)\s*(?:child|children|kids?)",
        r"(?i)board\s*of\s*directors|\bbod\b",
        r"(\d{3})+",
        r"[(+*]+(x)",
    ],
)
def test_ordinary_patterns_pass(pattern: str) -> None:
    assert pattern_error(pattern) is None


def test_overlong_invalid_and_verbose_patterns_are_refused() -> None:
    assert pattern_error("a" * (MAX_PATTERN_LENGTH + 1)) == (
        f"Derivation patterns must be at most {MAX_PATTERN_LENGTH} characters."
    )
    assert "not a valid regular expression" in (pattern_error("[") or "")
    assert "verbose mode" in (pattern_error(r"(?x)(a +) +") or "")
    assert pattern_error(12) == "Derivation patterns must be text."


def test_rule_check_reads_every_regex_case() -> None:
    rule = {
        "op": "regex_case",
        "source": "category",
        "cases": [{"pattern": r"(?i)married", "value": "M"}, {"pattern": r"(a|aa)+$"}],
    }
    assert "(a|aa)+$" in (rule_pattern_error(rule) or "")
    assert rule_pattern_error({"op": "regex_extract", "pattern": r"Grade\s+(\d+)"}) is None
    assert rule_pattern_error({"op": "value_map", "mappings": []}) is None


def test_a_stored_unsafe_pattern_never_runs() -> None:
    """Rules saved before the check existed are refused by the engine too."""
    rule = {"op": "regex_extract", "source": "category", "pattern": r"(a+)+$"}
    assert derive({"category": "a" * 40 + "!"}, [_schema("grade", rule)]) == {}
    with pytest.raises(re.error, match="unsafe derivation pattern"):
        apply_rule(rule, {"category": "aaa!"})


def test_patterns_only_read_the_start_of_an_oversized_value() -> None:
    rule = {"op": "regex_extract", "source": "category", "pattern": r"Grade\s+(\d+)"}
    near = "x" * (MAX_MATCH_INPUT - 20) + " Grade 7"
    far = "x" * MAX_MATCH_INPUT + " Grade 7"
    assert derive({"category": near}, [_schema("grade", rule)]) == {"grade": "7"}
    assert derive({"category": far}, [_schema("grade", rule)]) == {}


GLOBAL_SCHEMA_ID = "00000000-0000-0000-0000-0000000de001"


@pytest.fixture
def schemas_api() -> Iterator[TestClient]:
    with SessionLocal() as db:
        db.query(EmployeeAttributeSchema).filter(
            EmployeeAttributeSchema.id == GLOBAL_SCHEMA_ID
        ).delete()
        db.add(EmployeeAttributeSchema(
            id=GLOBAL_SCHEMA_ID, client_id=None, attribute_id="redos_probe",
            display_name="ReDoS probe", data_type="string",
        ))
        db.commit()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="00000000-0000-0000-0000-0000000de0ff",
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role="broker_admin",
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        with SessionLocal() as db:
            db.query(EmployeeAttributeSchema).filter(
                EmployeeAttributeSchema.id == GLOBAL_SCHEMA_ID
            ).delete()
            db.commit()


def test_attribute_endpoints_refuse_unsafe_patterns(schemas_api: TestClient) -> None:
    unsafe = {"op": "regex_extract", "source": "category", "pattern": r"(\w+)*$"}
    created = schemas_api.post(
        "/api/v1/schemas/employee-attributes",
        json={
            "attribute_id": "redos_new", "display_name": "ReDoS", "data_type": "string",
            "derivation_rule": unsafe,
        },
    )
    patched = schemas_api.patch(
        f"/api/v1/schemas/employee-attributes/{GLOBAL_SCHEMA_ID}",
        json={"derivation_rule": {"op": "regex_case", "source": "category",
                                  "cases": [{"pattern": r"(a|aa)+", "value": "A"}]}},
    )
    safe = schemas_api.patch(
        f"/api/v1/schemas/employee-attributes/{GLOBAL_SCHEMA_ID}",
        json={"derivation_rule": {"op": "regex_extract", "source": "category",
                                  "pattern": r"Grade\s+(\d+)"}},
    )

    assert created.status_code == 422
    assert "exponential time" in created.json()["detail"]
    assert patched.status_code == 422
    assert "(a|aa)+" in patched.json()["detail"]
    assert safe.status_code == 200, safe.text
    assert safe.json()["derivation_rule"]["pattern"] == r"Grade\s+(\d+)"
