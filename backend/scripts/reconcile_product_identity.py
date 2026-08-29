"""Reconcile duplicate same-code product records for one policy year.

A placement slip can historically have stored its categories on a global
catalog Product while guided setup confirmation created a client-owned Product
with the same code. This script keeps the client-owned setup product, deletes
only untouched setup-seeded duplicate categories, re-parents the original slip
categories/terms/pricing, and re-runs matching.

Dry-run is the default. Applying requires an explicit non-existing backup path;
the backup contains configuration IDs and JSON only (no names, staff IDs, email,
or other roster PII).

Examples:
  python -m scripts.reconcile_product_identity --firm-id <id> \
      --policy-year <id> --code GCGP
  python -m scripts.reconcile_product_identity --firm-id <id> \
      --policy-year <id> --code GCGP --apply --backup ./var/gcgp-backup.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.db.session import SessionLocal
from app.db.tenancy import set_search_path
from app.models import (
    Category,
    Employee,
    EmployeePlanOverride,
    EnrollmentElection,
    EnrollmentWindow,
    FlexPricing,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
)
from app.models.category import SourceKind
from app.services.matching_engine import match_policy_year

_TERM_FIELDS = (
    "coverage_start",
    "coverage_end",
    "gst_included",
    "gst_rate",
    "free_cover_limit",
    "nel_age_limit",
    "underwriting_required",
    "policy_number",
    "pre_hosp_days",
    "post_hosp_days",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _row_backup(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _json_value(getattr(row, field)) for field in fields}


def _merge_term(
    db: Session,
    source_terms: list[ProductTerm],
    target_term: ProductTerm | None,
    target_product_id: str,
) -> tuple[bool, bool]:
    if len(source_terms) > 1:
        raise RuntimeError("Multiple source policy-term rows are ambiguous.")
    if not source_terms:
        return False, False
    source = source_terms[0]
    if target_term is None:
        source.product_id = target_product_id
        return True, False
    for field in _TERM_FIELDS:
        source_value = getattr(source, field)
        target_value = getattr(target_term, field)
        if source_value is None:
            continue
        if target_value is None:
            setattr(target_term, field, source_value)
        elif target_value != source_value:
            raise RuntimeError(f"Conflicting ProductTerm field: {field}.")
    db.delete(source)
    return False, True


def reconcile(
    db: Session,
    *,
    broker_firm_id: str,
    policy_year_id: str,
    code: str,
    operator_id: str,
    apply: bool,
    backup_path: Path | None,
) -> dict[str, Any]:
    policy_year = db.get(PolicyYear, policy_year_id)
    if policy_year is None:
        raise RuntimeError("Policy year not found in the selected firm schema.")
    normalized_code = code.strip().upper()
    setup = db.scalar(
        select(ProductSetup).where(
            ProductSetup.policy_year_id == policy_year_id,
            func.upper(ProductSetup.product_code) == normalized_code,
        )
    )
    products = list(
        db.scalars(
            select(Product).where(func.upper(Product.code) == normalized_code)
        ).all()
    )
    by_id = {product.id: product for product in products}
    target = (
        by_id.get(setup.materialized_product_id or "")
        if setup is not None
        else None
    )
    if target is None:
        owned = [product for product in products if product.client_id == policy_year.client_id]
        if len(owned) != 1:
            raise RuntimeError("Could not identify one client-owned canonical product.")
        target = owned[0]
    if target.client_id != policy_year.client_id:
        raise RuntimeError("The setup product is not owned by this policy year's client.")
    source_ids = [product.id for product in products if product.id != target.id]
    if not source_ids:
        return {
            "ready": True,
            "changed": False,
            "code": normalized_code,
            "message": "No duplicate product identity remains.",
        }

    source_categories = list(
        db.scalars(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.in_(source_ids),
            )
        ).all()
    )
    target_categories = list(
        db.scalars(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id == target.id,
            )
        ).all()
    )
    if not source_categories or not target_categories:
        raise RuntimeError(
            "Expected both slip categories and duplicate setup categories; refusing "
            "to infer a different repair."
        )
    if any(
        category.source != SourceKind.system_generated.value
        or not str(category.source_ref or "").startswith("placement_slip://")
        for category in source_categories
    ):
        raise RuntimeError("A source category is not an untouched placement-slip row.")
    if any(
        category.source != SourceKind.manual.value
        or category.source_ref != "product_setup"
        or category.human_modified
        for category in target_categories
    ):
        raise RuntimeError(
            "A duplicate setup category was edited; manual reconciliation is required."
        )

    affected_product_ids = [target.id, *source_ids]
    election_refs = db.scalar(
        select(func.count(EnrollmentElection.id)).where(
            EnrollmentElection.policy_year_id == policy_year_id,
            EnrollmentElection.product_id.in_(affected_product_ids),
        )
    ) or 0
    override_refs = db.scalar(
        select(func.count(EmployeePlanOverride.id)).where(
            EmployeePlanOverride.policy_year_id == policy_year_id,
            EmployeePlanOverride.product_id.in_(affected_product_ids),
        )
    ) or 0
    if election_refs or override_refs:
        raise RuntimeError(
            "Existing elections or coverage overrides reference the duplicate products; "
            "automatic repair is unsafe."
        )

    pricing = db.scalar(
        select(FlexPricing).where(FlexPricing.policy_year_id == policy_year_id)
    )
    blocks = (
        (pricing.pricing or {}).get("products")
        if pricing is not None and isinstance(pricing.pricing, dict)
        else None
    )
    source_price_ids = [pid for pid in source_ids if isinstance(blocks, dict) and pid in blocks]
    if len(source_price_ids) > 1 or (
        source_price_ids and isinstance(blocks, dict) and target.id in blocks
    ):
        raise RuntimeError("Pricing exists on more than one duplicate product record.")

    source_terms = list(
        db.scalars(
            select(ProductTerm).where(
                ProductTerm.policy_year_id == policy_year_id,
                ProductTerm.product_id.in_(source_ids),
            )
        ).all()
    )
    target_term = db.scalar(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.product_id == target.id,
        )
    )
    generated_plans = list(
        db.scalars(
            select(Plan).where(
                Plan.policy_year_id == policy_year_id,
                Plan.product_id.in_(source_ids),
                Plan.source == SourceKind.system_generated.value,
            )
        ).all()
    )
    employees = list(
        db.scalars(select(Employee).where(Employee.policy_year_id == policy_year_id)).all()
    )
    windows = list(
        db.scalars(
            select(EnrollmentWindow).where(
                EnrollmentWindow.policy_year_id == policy_year_id
            )
        ).all()
    )

    backup = {
        "policy_year_id": policy_year_id,
        "code": normalized_code,
        "target_product_id": target.id,
        "source_product_ids": source_ids,
        "source_categories": [
            _row_backup(
                category,
                (
                    "id",
                    "product_id",
                    "status",
                    "source",
                    "source_ref",
                    "matching_rule",
                    "rule_validation",
                    "plan_assignments",
                ),
            )
            for category in source_categories
        ],
        "target_categories": [
            _row_backup(
                category,
                (
                    "id",
                    "product_id",
                    "status",
                    "source",
                    "source_ref",
                    "matching_rule",
                    "rule_validation",
                    "plan_assignments",
                ),
            )
            for category in target_categories
        ],
        "source_terms": [
            _row_backup(term, ("id", "product_id", *_TERM_FIELDS))
            for term in source_terms
        ],
        "target_term": (
            _row_backup(target_term, ("id", "product_id", *_TERM_FIELDS))
            if target_term is not None
            else None
        ),
        "pricing": pricing.pricing if pricing is not None else None,
        "employee_matches": [
            {
                "employee_id": employee.id,
                "matched_category_id": employee.matched_category_id,
                "matched_categories": employee.matched_categories,
            }
            for employee in employees
        ],
        "window_scopes": [
            {"window_id": window.id, "product_scope": window.product_scope}
            for window in windows
        ],
    }
    result = {
        "ready": True,
        "changed": True,
        "applied": apply,
        "code": normalized_code,
        "target_product_id": target.id,
        "source_product_count": len(source_ids),
        "slip_categories_to_reparent": len(source_categories),
        "setup_categories_to_remove": len(target_categories),
        "generated_plans_to_remove": len(generated_plans),
        "pricing_to_rekey": bool(source_price_ids),
        "employees_to_rematch": len(employees),
    }
    if not apply:
        db.rollback()
        return result
    if backup_path is None:
        raise RuntimeError("--backup is required with --apply.")
    if backup_path.exists():
        raise RuntimeError("Backup path already exists; choose a new path.")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, indent=2, default=_json_value), encoding="utf-8")

    target_category_ids = {category.id for category in target_categories}
    for category in target_categories:
        db.delete(category)
    db.flush()
    for category in source_categories:
        category.product_id = target.id
    for plan in generated_plans:
        db.delete(plan)
    _merge_term(db, source_terms, target_term, target.id)
    if source_price_ids and pricing is not None and isinstance(blocks, dict):
        bag = dict(pricing.pricing or {})
        updated_blocks = dict(blocks)
        updated_blocks[target.id] = updated_blocks.pop(source_price_ids[0])
        bag["products"] = updated_blocks
        pricing.pricing = bag
        flag_modified(pricing, "pricing")
    for window in windows:
        if not isinstance(window.product_scope, list):
            continue
        updated_scope = [
            target.id if product_id in source_ids else product_id
            for product_id in window.product_scope
        ]
        window.product_scope = list(dict.fromkeys(updated_scope))
        flag_modified(window, "product_scope")
    db.flush()

    user = CurrentUser(
        user_id=operator_id,
        broker_firm_id=broker_firm_id,
        client_id=policy_year.client_id,
        role="system_admin",
    )
    summary = match_policy_year(db, policy_year_id, user)
    # The rematch must have removed every deleted category id from the live JSON.
    stale = sum(
        1
        for employee in employees
        if employee.matched_category_id in target_category_ids
        or any(
            isinstance(match, dict) and match.get("category_id") in target_category_ids
            for match in (employee.matched_categories or [])
        )
    )
    if stale:
        raise RuntimeError("Re-match left stale category references; transaction rolled back.")
    write_audit(
        db,
        user,
        action="reconcile_duplicate_product_identity",
        entity_type="product",
        entity_id=target.id,
        before={"source_product_ids": source_ids, "code": normalized_code},
        after={**result, "employees_matched": summary.employees_matched},
    )
    db.commit()
    return {**result, "employees_matched": summary.employees_matched}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firm-id", required=True)
    parser.add_argument("--policy-year", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--operator-id", default="system-data-repair")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()

    with SessionLocal() as db:
        set_search_path(db, args.firm_id)
        result = reconcile(
            db,
            broker_firm_id=args.firm_id,
            policy_year_id=args.policy_year,
            code=args.code,
            operator_id=args.operator_id,
            apply=args.apply,
            backup_path=args.backup,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
