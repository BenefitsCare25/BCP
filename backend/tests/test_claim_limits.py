from app.services.claim_intake import (
    SUB_TYPE_TCM,
    benefit_row_for_scope,
    claim_scope_catalog,
)
from app.services.claim_limits import (
    enforceable_policy_year_amount,
    normalize_limit_setting,
    suggested_limit_setting,
    validate_schedule_limits,
)


def setting(
    *,
    basis: str = "policy_year",
    amount: float | None = 500,
    scopes: list[str] | None = None,
    status: str = "verified",
) -> dict:
    return {
        "basis": basis,
        "amount": amount,
        "currency": "SGD",
        "display": "S$500 per policy year",
        "claim_scope_codes": scopes or [],
        "status": status,
        "source": "manual",
    }


def test_only_policy_year_amount_is_enforceable():
    assert enforceable_policy_year_amount(setting()) == 500
    assert enforceable_policy_year_amount(setting(basis="per_visit")) is None
    assert enforceable_policy_year_amount(setting(status="not_limit")) is None
    assert enforceable_policy_year_amount(setting(amount=None)) is None
    assert enforceable_policy_year_amount(setting(amount=-500)) is None
    foreign = setting()
    foreign["currency"] = "USD"
    assert normalize_limit_setting(foreign) is None


def test_detected_settings_preserve_basis_without_enforcing_per_unit():
    annual = suggested_limit_setting("S$1,200 per policy year")
    visit = suggested_limit_setting("S$80/visit")
    assert annual and annual["basis"] == "policy_year" and annual["amount"] == 1200
    assert visit and visit["basis"] == "per_visit" and visit["amount"] is None
    assert annual["status"] == visit["status"] == "needs_review"


def test_explicit_mapping_wins_over_legacy_tcm_keyword():
    schedule = {
        "items": [
            {"name": "TCM", "value": "S$20/visit", "claim_limit": setting(
                basis="per_visit", amount=None, scopes=[]
            )},
            {"name": "Alternative medicine", "value": "S$300/year", "claim_limit": setting(
                scopes=["gp_tcm"]
            )},
        ]
    }
    assert benefit_row_for_scope(schedule, "gp_tcm", SUB_TYPE_TCM) == "Alternative medicine"


def test_not_limit_can_still_map_a_claim_type_without_enforcement():
    schedule = {
        "items": [
            {
                "name": "TCM",
                "value": "As charged",
                "claim_limit": setting(
                    basis="informational",
                    amount=None,
                    scopes=["gp_tcm"],
                    status="not_limit",
                ),
            }
        ]
    }
    assert benefit_row_for_scope(schedule, "gp_tcm", SUB_TYPE_TCM) == "TCM"


def test_duplicate_or_unknown_scope_mapping_is_rejected():
    schedule = {
        "items": [
            {"name": "A", "claim_limit": setting(scopes=["standard"])},
            {"name": "B", "claim_limit": setting(scopes=["standard", "made_up"])},
        ]
    }
    errors = validate_schedule_limits(schedule, valid_scope_codes={"standard"})
    assert any("mapped to both" in error for error in errors)
    assert any("unknown claim type" in error for error in errors)


def test_broker_catalog_includes_optional_gp_riders_before_mapping():
    assert [scope.code for scope in claim_scope_catalog("GP", "GP")] == [
        "standard",
        "gp_tcm",
        "gp_physiotherapy",
    ]
