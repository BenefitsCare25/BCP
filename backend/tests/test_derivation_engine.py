"""Unit tests for schema-driven attribute derivation."""
from __future__ import annotations

from app.models.schema_def import EmployeeAttributeSchema
from app.services.derivation_engine import derive


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
