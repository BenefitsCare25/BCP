"""Broker config: per-claim-type AI review rule setup.

One row per (client, claim type) — see ``services/claim_review_configs.py``.
Edited on the Claims page "Review rules" tab. A claim type with no row keeps
the in-code defaults (no lazy seeding — absence IS the default; the UI shows
a "Default" badge). Also hosts the cross-company import: a broker duplicates
another accessible company's setup for matching claim types (e.g. copy an
Outpatient GP rule setup into a newly onboarded company).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_claim_configuration, require_client_id
from app.core.identity import accessible_clients, assert_client_accessible
from app.core.optimistic_lock import assert_not_stale
from app.core.portal_auth import active_policy_year
from app.db.session import get_db
from app.models import ClaimReviewConfig, Client, FlexScheme, Product
from app.models.claim import CLAIM_KIND_FLEX, CLAIM_KIND_INSURED
from app.schemas.claims import (
    CLAIM_REVIEW_PORTAL_FIELDS,
    ClaimReviewConfigIn,
    ClaimReviewConfigOut,
    ClaimReviewConfigUpdateIn,
    ImportReviewConfigsIn,
    ImportReviewConfigsOut,
    ImportSourceCompanyOut,
    ReviewAIRuleModel,
    ReviewClaimTypeOut,
    ReviewDefaultConfigOut,
    ReviewFieldMapModel,
    ReviewPromptPreviewOut,
    ReviewScopeOptionsOut,
    SourceReviewConfigOut,
)
from app.services.claim_ai import build_claim_review_prompt
from app.services.claim_intake import claim_profile_for
from app.services.claim_review_configs import (
    config_from_row,
    config_rows,
    default_review_config,
    find_config_row,
    type_key,
)
from app.services.product_terms import product_ids_in_year

router = APIRouter(
    prefix="/claim-review-configs",
    tags=["claim-review-configs"],
    dependencies=[Depends(require_claim_configuration)],
)


def _out(row: ClaimReviewConfig) -> ClaimReviewConfigOut:
    # Read through the defensive config builder so legacy/hand-edited JSON
    # shapes render instead of failing response validation. ClaimReviewConfigOut
    # deliberately carries no write-side constraints for the same reason: a bad
    # row must stay listable (and deletable), never 500 the whole surface.
    cfg = config_from_row(row)
    return ClaimReviewConfigOut(
        id=row.id,
        claim_kind=row.claim_kind,
        claim_key=row.claim_key,
        key=type_key(row.claim_kind, row.claim_key),
        display_label=row.display_label,
        enabled=row.enabled,
        field_maps=[ReviewFieldMapModel(**m) for m in cfg.field_maps],
        ai_rules=[
            ReviewAIRuleModel(
                id=r.id, rule=r.rule, category=r.category, severity=r.severity  # type: ignore[arg-type]
            )
            for r in cfg.ai_rules
        ],
        required_documents=list(cfg.required_documents or ()),
        updated_at=row.updated_at,
    )


def _own_row(db: Session, config_id: str, client_id: str) -> ClaimReviewConfig:
    row = db.get(ClaimReviewConfig, config_id)
    if row is None or row.client_id != client_id:
        # Same not-403 convention as tenant scoping everywhere else.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review config not found")
    return row


def _payload(body: ClaimReviewConfigIn) -> dict[str, Any]:
    # claim_key / display_label are already normalized (and rejected when
    # blank) by ClaimReviewConfigIn's validator, so what validated is exactly
    # what gets stored.
    return {
        "claim_kind": body.claim_kind,
        "claim_key": body.claim_key,
        "display_label": body.display_label,
        "enabled": body.enabled,
        "field_maps": [m.model_dump(exclude_none=True) for m in body.field_maps],
        "ai_rules": [
            {
                **r.model_dump(exclude_none=True),
                "id": r.id or f"rule_{uuid4().hex}",
            }
            for r in body.ai_rules
        ],
        "required_documents": [
            d for d in (" ".join(s.split()) for s in body.required_documents) if d
        ],
    }


def _assert_key_free(
    db: Session, client_id: str, body: ClaimReviewConfigIn, exclude_id: str | None
) -> None:
    existing = find_config_row(db, client_id, body.claim_kind, body.claim_key)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_claim_type",
                "message": (
                    f'"{existing.display_label}" already covers this claim type — '
                    "edit that setup instead."
                ),
            },
        )


def _default_config_out() -> ReviewDefaultConfigOut:
    cfg = default_review_config()
    return ReviewDefaultConfigOut(
        field_maps=[ReviewFieldMapModel(**m) for m in cfg.field_maps],
        ai_rules=[
            ReviewAIRuleModel(
                id=r.id, rule=r.rule, category=r.category, severity=r.severity  # type: ignore[arg-type]
            )
            for r in cfg.ai_rules
        ],
        required_documents=[],
    )


@router.get("", response_model=list[ClaimReviewConfigOut])
def list_review_configs(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimReviewConfigOut]:
    client_id = require_client_id(user)
    return [_out(r) for r in config_rows(db, client_id)]


def _flex_category_names(db: Session, policy_year_id: str) -> list[str]:
    """Claimable flex benefit-category names of the year's scheme, de-duped.

    ``FlexScheme.scheme`` is unvalidated JSON (AI-extracted then broker-edited),
    so every level is shape-guarded: a scheme stored as anything but an object
    must not raise out of the options endpoint and take the whole AI-extraction
    tab down with it.
    """
    row = (
        db.execute(select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id))
        .scalars()
        .first()
    )
    scheme = row.scheme if row is not None else None
    if not isinstance(scheme, dict):
        return []
    tiers = scheme.get("tiers")
    names: list[str] = []
    folded: set[str] = set()
    for tier in tiers if isinstance(tiers, list) else []:
        if not isinstance(tier, dict):
            continue
        categories = tier.get("benefit_categories")
        for cat in categories if isinstance(categories, list) else []:
            if not isinstance(cat, dict):
                continue
            name = str(cat.get("name") or "").strip()
            # Missing "claimable" reads as claimable — mirrors the member claim
            # form's defensive reading.
            if not name or not cat.get("claimable", True):
                continue
            if name.casefold() not in folded:
                folded.add(name.casefold())
                names.append(name)
    return sorted(names, key=str.casefold)


@router.get("/options", response_model=ReviewScopeOptionsOut)
def review_scope_options(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewScopeOptionsOut:
    """The company's claim types (the config vocabulary) + the default setup.

    Insured claim types = the member-claimable products of the CURRENT benefit
    year; flex claim types = that year's flex scheme benefit categories. With
    no year flagged current there is no vocabulary at all — reported as
    ``has_current_year=False`` rather than an indistinguishable empty list.
    """
    client_id = require_client_id(user)
    claim_types: list[ReviewClaimTypeOut] = []
    year = active_policy_year(db, client_id)
    if year is not None:
        # Products in the year = plans UNION product-bound categories, the same
        # set `product_ids_in_year` resolves for coverage periods and the setup
        # list. Joining on Plan alone dropped a product configured with
        # categories but no plan rows.
        pids = product_ids_in_year(db, year.id)
        products = (
            list(db.execute(select(Product).where(Product.id.in_(pids))).scalars())
            if pids
            else []
        )
        seen: set[str] = set()
        for p in sorted(products, key=lambda p: (p.display_name or p.code or "")):
            code = (p.code or "").strip()
            if not code or code.upper() in seen:
                continue
            seen.add(code.upper())
            profile = claim_profile_for(code)
            if not profile.member_claimable:
                continue
            claim_types.append(
                ReviewClaimTypeOut(
                    claim_kind=CLAIM_KIND_INSURED,
                    claim_key=code,
                    key=type_key(CLAIM_KIND_INSURED, code),
                    display_label=profile.claim_type_label or p.display_name or code,
                    sub_types=list(profile.sub_types),
                )
            )
        claim_types.extend(
            ReviewClaimTypeOut(
                claim_kind=CLAIM_KIND_FLEX,
                claim_key=n,
                key=type_key(CLAIM_KIND_FLEX, n),
                display_label=n,
            )
            for n in _flex_category_names(db, year.id)
        )
    return ReviewScopeOptionsOut(
        claim_types=claim_types,
        default_config=_default_config_out(),
        portal_fields=list(CLAIM_REVIEW_PORTAL_FIELDS),
        has_current_year=year is not None,
    )


@router.post("", response_model=ClaimReviewConfigOut, status_code=status.HTTP_201_CREATED)
def create_review_config(
    body: ClaimReviewConfigIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimReviewConfigOut:
    client_id = require_client_id(user)
    _assert_key_free(db, client_id, body, exclude_id=None)
    data = _payload(body)
    row = ClaimReviewConfig(client_id=client_id, **data)
    db.add(row)
    db.flush()
    write_audit(
        db, user, "claim_review_config.created", "claim_review_config", row.id, after=data
    )
    db.commit()
    return _out(row)


@router.put("/{config_id}", response_model=ClaimReviewConfigOut)
def update_review_config(
    config_id: str,
    body: ClaimReviewConfigUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimReviewConfigOut:
    client_id = require_client_id(user)
    row = _own_row(db, config_id, client_id)
    assert_not_stale(
        expected=body.expected_updated_at,
        actual=row.updated_at,
        label="This review-rule setup",
    )
    _assert_key_free(db, client_id, body, exclude_id=row.id)
    data = _payload(body)
    before = {
        "claim_kind": row.claim_kind,
        "claim_key": row.claim_key,
        "display_label": row.display_label,
        "enabled": row.enabled,
        "field_maps": row.field_maps,
        "ai_rules": row.ai_rules,
        "required_documents": row.required_documents,
    }
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(UTC)
    write_audit(
        db, user, "claim_review_config.updated", "claim_review_config", row.id,
        before=before, after=data,
    )
    db.commit()
    return _out(row)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review_config(
    config_id: str,
    expected_updated_at: datetime | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Deleting a row reverts that claim type to the in-code defaults."""
    client_id = require_client_id(user)
    row = _own_row(db, config_id, client_id)
    assert_not_stale(
        expected=expected_updated_at,
        actual=row.updated_at,
        label="This review-rule setup",
    )
    write_audit(
        db, user, "claim_review_config.deleted", "claim_review_config", row.id,
        before={"claim_kind": row.claim_kind, "claim_key": row.claim_key,
                "display_label": row.display_label},
    )
    db.delete(row)
    db.commit()


@router.post("/preview", response_model=ReviewPromptPreviewOut)
def preview_review_prompt(
    body: ClaimReviewConfigIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReviewPromptPreviewOut:
    """Stateless prompt preview of the editor's CURRENT state — what the AI
    review call will look like, with runtime data as placeholder markers."""
    require_client_id(user)
    data = _payload(body)
    claim_fields: dict[str, Any] = {
        str(m["portal_field"]): f"<{m['portal_field']}>" for m in data["field_maps"]
    }
    claim_fields["claim_kind"] = data["claim_kind"]
    if data["claim_kind"] == CLAIM_KIND_FLEX:
        claim_fields["flex_category_name"] = data["claim_key"]
    else:
        claim_fields["product_code"] = data["claim_key"]
    rules = [
        f"[{str(r.get('severity', 'critical')).upper()}] {r.get('rule')}"
        for r in data["ai_rules"]
    ]
    prompt = build_claim_review_prompt(
        claim_fields=claim_fields,
        documents=[
            {
                "file_name": "<uploaded document>",
                "document_type": "<detected document type>",
                "fields": [
                    {"label": "<extracted field>", "value": "<extracted value>"}
                ],
            }
        ],
        field_maps=data["field_maps"],
        ai_rules=rules,
        required_documents=data["required_documents"]
        or ["<derived automatically from the claim type>"],
    )
    return ReviewPromptPreviewOut(prompt=prompt)


def _accessible_source(
    db: Session, user: CurrentUser, source_client_id: str
) -> Client:
    """Resolve the import source. 404 (never 403) on anything inaccessible.

    Beyond the principal's own access, the source must sit in the SAME broker
    firm as the active client: on Postgres each firm's tenant tables live in
    their own schema, so a cross-firm read through the active firm's
    ``search_path`` would silently see nothing (a system_admin would otherwise
    pass the access check and "import" zero rows). The SQLite suite cannot
    exercise that boundary — hence the explicit guard.
    """
    active_id = require_client_id(user)
    if source_client_id == active_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose a different company to import from.",
        )
    source = assert_client_accessible(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        client_id=source_client_id,
        db=db,
    )
    active = db.get(Client, active_id)
    if (
        source is None
        or active is None
        or source.broker_firm_id != active.broker_firm_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return source


@router.get("/sources", response_model=list[ImportSourceCompanyOut])
def import_source_companies(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ImportSourceCompanyOut]:
    """Companies this user may import a rule setup FROM.

    Server-authoritative so the picker can't offer a company the import would
    reject: accessible to the principal AND in the active client's broker firm
    (a `system_admin`'s `accessible_clients` spans every firm, but a cross-firm
    read is a no-op on Postgres — see `_accessible_source`).
    """
    active_id = require_client_id(user)
    active = db.get(Client, active_id)
    if active is None:
        return []
    out: list[ImportSourceCompanyOut] = []
    for client in accessible_clients(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        db=db,
    ):
        if client.id == active_id or client.broker_firm_id != active.broker_firm_id:
            continue
        out.append(
            ImportSourceCompanyOut(
                id=client.id,
                name=client.name,
                configured_count=len(config_rows(db, client.id)),
            )
        )
    return out


@router.get("/from/{source_client_id}", response_model=list[SourceReviewConfigOut])
def source_review_configs(
    source_client_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SourceReviewConfigOut]:
    """Another company's configured claim types, offered for import."""
    source = _accessible_source(db, user, source_client_id)
    out: list[SourceReviewConfigOut] = []
    for row in config_rows(db, source.id):
        cfg = config_from_row(row)
        out.append(
            SourceReviewConfigOut(
                id=row.id,
                claim_kind=row.claim_kind,
                claim_key=row.claim_key,
                key=type_key(row.claim_kind, row.claim_key),
                display_label=row.display_label,
                enabled=row.enabled,
                field_map_count=len(cfg.field_maps),
                rule_count=len(cfg.ai_rules),
                required_document_count=len(cfg.required_documents or ()),
            )
        )
    return out


@router.post("/import", response_model=ImportReviewConfigsOut)
def import_review_configs(
    body: ImportReviewConfigsIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportReviewConfigsOut:
    """Duplicate selected claim-type setups from another company. Each
    imported setup lands on the matching (claim_kind, claim_key) of the
    active company — created when absent, OVERWRITTEN when already
    customized (the UI warns before overwriting)."""
    client_id = require_client_id(user)
    source = _accessible_source(db, user, body.source_client_id)
    imported: list[ClaimReviewConfig] = []
    for config_id in dict.fromkeys(body.config_ids):
        src = db.get(ClaimReviewConfig, config_id)
        if src is None or src.client_id != source.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Review config not found")
        resolved = config_from_row(src)
        try:
            import_body = ClaimReviewConfigIn.model_validate(
                {
                    "claim_kind": src.claim_kind,
                    "claim_key": src.claim_key,
                    "display_label": src.display_label,
                    "enabled": src.enabled,
                    "field_maps": list(resolved.field_maps),
                    "ai_rules": [
                        {
                            "id": rule.id,
                            "rule": rule.rule,
                            "category": rule.category,
                            "severity": rule.severity,
                        }
                        for rule in resolved.ai_rules
                    ],
                    "required_documents": list(resolved.required_documents or ()),
                }
            )
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "The source setup contains unsupported or invalid review fields. "
                "Correct it in the source company before importing.",
            ) from exc
        data = _payload(import_body)
        target = find_config_row(db, client_id, src.claim_kind, src.claim_key)
        if target is None:
            target = ClaimReviewConfig(client_id=client_id, **data)
            db.add(target)
        else:
            assert_not_stale(
                expected=body.target_versions.get(
                    type_key(src.claim_kind, src.claim_key)
                ),
                actual=target.updated_at,
                label=f'Rules for "{target.display_label}"',
            )
            for field, value in data.items():
                setattr(target, field, value)
            target.updated_at = datetime.now(UTC)
        db.flush()
        imported.append(target)
    write_audit(
        db, user, "claim_review_config.imported", "claim_review_config", None,
        after={
            "source_client_id": source.id,
            "claim_types": [(r.claim_kind, r.claim_key) for r in imported],
        },
    )
    db.commit()
    return ImportReviewConfigsOut(imported=[_out(r) for r in imported])
