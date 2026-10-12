"""Setup-draft persistence for the guided product setup (`api/v1/product_setups`).

Template resolution for the setup form, draft lookup and upsert with optimistic
concurrency (stale writes are refused), the saved-removal guard, slip-prefilled
draft seeding, answer suggestions from earlier confirmed setups, and the
year-usage queries behind the setup-product list and the remove-product guard.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth import CurrentUser
from app.core.deps import tenant_or_global
from app.models import (
    Category,
    Claim,
    EmployeePlanOverride,
    EnrollmentElection,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
    UnderwritingCase,
)
from app.models.product_setup import ProductSetupOrigin, ProductSetupStatus
from app.schemas.product_setup import FieldSuggestions, SetupSaveIn
from app.services.dynamic_template import (
    generic_starter_template,
    merge_file_overlay,
    synthesize_template,
)
from app.services.product_templates import ProductTemplate, get_template

# ── Field suggestions ───────────────────────────────────────────────────────


_MAX_SUGGESTIONS = 8
# Cap how many confirmed setups we scan for suggestions — recent ones carry the
# relevant wordings, so we don't stream every year's full answers blob.
_SUGGESTION_SCAN_LIMIT = 25


def _collect_section(dst: dict[str, list[str]], section: Any) -> None:
    if not isinstance(section, dict):
        return
    for key, raw in section.items():
        # Suggestions back FREE-TEXT pickers only. `header.entities` is a token
        # list, and str()-ing it would offer the broker a Python repr
        # ("['Acme Pte Ltd', ...]") as a pickable value.
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if not val:
            continue
        bucket = dst.setdefault(str(key), [])
        if val not in bucket and len(bucket) < _MAX_SUGGESTIONS:
            bucket.append(val)


def _collect_scalar(bucket: list[str], raw: Any) -> None:
    val = str(raw or "").strip()
    if val and val not in bucket and len(bucket) < _MAX_SUGGESTIONS:
        bucket.append(val)


def _aggregate_suggestions(rows: Sequence[ProductSetup]) -> FieldSuggestions:
    out = FieldSuggestions()
    for setup in rows:
        answers = setup.answers or {}
        _collect_section(out.header, answers.get("header"))
        _collect_section(out.eligibility, answers.get("eligibility"))
        _collect_scalar(out.participation, answers.get("participation"))
        _collect_scalar(out.cover_description, answers.get("cover_description"))
    return out


# ── Template resolution ─────────────────────────────────────────────────────


def _resolve_template(
    db: Session,
    policy_year_id: str,
    code: str,
    client_id: str,
    *,
    need_structure: bool = True,
) -> ProductTemplate | None:
    """Structure for a product's setup form: the hand-authored JSON template if
    one exists, otherwise synthesized from the product's slip-derived Plan/
    Category rows in this policy year (or a blank starter when it has none).
    None when neither a template file nor a catalog product exists.

    ``need_structure=False`` skips the Plan/Category scan and returns the file
    template (or starter scaffold) — for callers (save/confirm) that only need the
    product's attributes (code/display_name/participation/basis_model/rate_model),
    not its actual plans/tiers/benefit lines.

    When the product has slip-derived Plan/Category rows, the synthesized
    structure (the client's real plans + benefit lines) is preferred and the
    hand-authored file template is overlaid for presentation (models and kinds)
    — so a slip with more plans than the canned template still shows
    every plan. Without slip rows, the file template (or starter) is used as the
    from-scratch scaffold.
    """
    file_tpl = get_template(code)
    product = db.execute(
        select(Product)
        .where(
            tenant_or_global(Product.client_id, client_id),
            func.upper(Product.code) == code.upper(),
        )
        # Prefer the client-specific row over a global (client_id NULL) one of the
        # same code, so a confirm-created client product (which holds the slip rows)
        # wins over the empty seeded global product.
        .order_by(Product.client_id.is_(None))
    ).scalars().first()
    if not need_structure:
        if file_tpl is not None:
            return file_tpl
        return generic_starter_template(product) if product is not None else None
    if product is None:
        return file_tpl
    synth = synthesize_template(db, policy_year_id, product)
    if synth is not None:
        return merge_file_overlay(synth, file_tpl)
    return file_tpl or generic_starter_template(product)


# ── Drafts ──────────────────────────────────────────────────────────────────


def _find_setup(
    db: Session,
    policy_year_id: str,
    product_code: str,
    *,
    lock: bool = False,
) -> ProductSetup | None:
    stmt = select(ProductSetup).where(
        ProductSetup.policy_year_id == policy_year_id,
        ProductSetup.product_code == product_code.upper(),
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _guard_saved_removals(
    db: Session, policy_year_id: str, code: str, body: SetupSaveIn, user: CurrentUser
) -> None:
    """Carry saved policy-number assignments forward, and refuse removals of
    saved assignments or setup entries unless the user is a firm owner."""
    from app.services.policy_numbers import require_assignment_removal_permission
    from app.services.setup_removals import require_setup_removal_permission

    old_setup = _find_setup(db, policy_year_id, code)
    if old_setup is None:
        return
    # A stale form must get the reload prompt, not a 403 for entries it never saw.
    _require_current_setup(old_setup, body)
    if "policy_number_mappings" not in body.answers:
        if "policy_number_mappings" in old_setup.answers:
            body.answers["policy_number_mappings"] = old_setup.answers["policy_number_mappings"]
    if "policy_number_mappings" in body.answers:
        require_assignment_removal_permission(
            old_setup.answers.get("policy_number_mappings"),
            body.answers["policy_number_mappings"], user.role,
        )
    require_setup_removal_permission(old_setup.answers, body.answers, user.role)


def _stale_setup(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": "stale_configuration", "message": message},
    )


def _require_current_setup(setup: ProductSetup, body: SetupSaveIn) -> None:
    """409 ``stale_configuration`` unless the write was made from the stored version."""
    if body.expected_updated_at is None:
        raise _stale_setup(
            "This setup has changed. Reload the latest version before saving."
        )
    expected = body.expected_updated_at
    current_utc = setup.updated_at.replace(tzinfo=None)
    expected_utc = (
        expected.astimezone(UTC).replace(tzinfo=None)
        if expected.tzinfo
        else expected
    )
    if current_utc != expected_utc:
        raise _stale_setup(
            "This setup was updated by another user. Reload the latest version before saving."
        )


def _upsert_draft(
    db: Session,
    policy_year_id: str,
    code: str,
    body: SetupSaveIn,
    *,
    lock: bool = False,
) -> ProductSetup:
    from app.services.el_report_rules import validate_report_rules

    try:
        validate_report_rules(body.answers)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    setup = _find_setup(db, policy_year_id, code, lock=lock)
    if setup is None:
        setup = ProductSetup(
            policy_year_id=policy_year_id,
            product_code=code.upper(),
            template_version=body.template_version,
            answers=dict(body.answers),
            status=ProductSetupStatus.draft,
            updated_at=datetime.now(UTC),
        )
        db.add(setup)
        db.flush()
    else:
        _require_current_setup(setup, body)
        if setup.status == ProductSetupStatus.confirmed and setup.materialized_product_id:
            from app.services.el_report_rules import apply_report_rules

            apply_report_rules(
                db, policy_year_id, setup.materialized_product_id, setup.answers,
                preserve_existing=True,
            )
        # Shallow-merge top-level sections so a partial body (e.g. a confirm that
        # only carries `plans`) can't silently wipe a previously-saved section
        # like `categories` or `rate_table`. A section is only replaced when the
        # caller explicitly sends that key.
        source = {
            key: value
            for key, value in (setup.answers or {}).items()
            if key in {"source_issues", "source_rate_schedules"}
        }
        setup.answers = {**(setup.answers or {}), **body.answers, **source}
        setup.template_version = body.template_version
        # SQLite CURRENT_TIMESTAMP has second precision; consecutive saves must
        # still receive distinct versions for optimistic concurrency checks.
        setup.updated_at = datetime.now(UTC)
    return setup


def seed_draft_from_slip(
    db: Session,
    policy_year_id: str,
    slip_id: str,
    product_code: str,
    answers: dict[str, Any],
    template_version: int,
) -> bool:
    """Create or replace a slip-prefilled draft for a product.

    A re-upload is the latest source for unconfirmed setup drafts, so saved
    answers are replaced wholesale. Confirmed setups stay protected because they
    have already been materialized into product configuration.
    """
    setup = _find_setup(db, policy_year_id, product_code)
    if setup is not None:
        if setup.status != ProductSetupStatus.draft or setup.materialized_product_id is not None:
            return False
        setup.answers = answers
        setup.template_version = template_version
        setup.origin = ProductSetupOrigin.placement_slip
        setup.origin_ref = slip_id
        flag_modified(setup, "answers")
        return True
    db.add(
        ProductSetup(
            policy_year_id=policy_year_id,
            product_code=product_code.upper(),
            template_version=template_version,
            answers=answers,
            status=ProductSetupStatus.draft,
            origin=ProductSetupOrigin.placement_slip,
            origin_ref=slip_id,
        )
    )
    return True


# ── Product usage across policy years ───────────────────────────────────────


# Member records that reference a product by id and cascade with its catalog row.
_PRODUCT_RECORD_MODELS: tuple[tuple[str, Any], ...] = (
    ("enrolment elections", EnrollmentElection),
    ("plan overrides", EmployeePlanOverride),
    ("underwriting cases", UnderwritingCase),
)


def _products_in_use(
    db: Session, product_ids: Sequence[str], *, outside_year: str | None = None
) -> set[str]:
    """The catalog rows among ``product_ids`` that some policy year references
    through its categories, plans, coverage terms or member records.
    ``outside_year`` ignores that one year's references."""
    if not product_ids:
        return set()
    referencing: tuple[Any, ...] = (
        Category, Plan, ProductTerm, *(model for _, model in _PRODUCT_RECORD_MODELS)
    )
    used: set[str] = set()
    for model in referencing:
        stmt = select(model.product_id).where(model.product_id.in_(product_ids))
        if outside_year is not None:
            stmt = stmt.where(model.policy_year_id != outside_year)
        used.update(db.execute(stmt.distinct()).scalars())
    return used


def _setup_codes_in_use(
    db: Session, client_id: str, *, outside_year: str | None = None
) -> set[str]:
    """Product codes that some policy year of the client holds a setup (draft or
    confirmed) for. ``outside_year`` ignores that one year's setups."""
    stmt = (
        select(ProductSetup.product_code)
        .join(PolicyYear, PolicyYear.id == ProductSetup.policy_year_id)
        .where(PolicyYear.client_id == client_id)
    )
    if outside_year is not None:
        stmt = stmt.where(ProductSetup.policy_year_id != outside_year)
    return {c.strip().upper() for c in db.execute(stmt).scalars()}


def _refuse_removal_with_records(
    db: Session, policy_year_id: str, pids: set[str], code: str
) -> None:
    """409 when members already hold enrolment, override, underwriting or claim
    records for this product in the year. Removing it would cascade-delete them
    or leave claims pointing at a product that no longer exists."""
    counts = {
        label: db.scalar(
            select(func.count()).select_from(model).where(
                model.policy_year_id == policy_year_id, model.product_id.in_(pids)
            )
        ) or 0
        for label, model in (_PRODUCT_RECORD_MODELS if pids else ())
    }
    counts["claims"] = db.scalar(
        select(func.count(Claim.id)).where(
            Claim.policy_year_id == policy_year_id,
            func.upper(Claim.product_code) == code,
        )
    ) or 0
    records = {label: int(n) for label, n in counts.items() if n}
    if records:
        summary = ", ".join(f"{n} {label}" for label, n in records.items())
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "product_has_member_records",
                "message": (
                    f"{code} cannot be removed because this benefit year already "
                    f"has {summary} for it."
                ),
                "records": records,
            },
        )
