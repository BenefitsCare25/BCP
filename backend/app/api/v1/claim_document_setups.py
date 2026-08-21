"""Broker configuration for independent documents owned by each claim type."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.claim_review_configs import review_scope_options
from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_claim_configuration, require_client_id
from app.core.optimistic_lock import assert_not_stale
from app.db.session import get_db
from app.models import ClaimDocumentSetup
from app.schemas.claims import (
    ClaimDocumentSetupIn,
    ClaimDocumentSetupOut,
    ClaimSetupDocument,
    DuplicateClaimDocumentSetupIn,
)
from app.services.claim_document_setups import (
    dump_documents,
    find_setup_row,
    resolve_setup,
)
from app.services.claim_intake import CLAIM_SCOPE_CODES, SCOPE_STANDARD, claim_scope_key

router = APIRouter(
    prefix="/claim-document-setups",
    tags=["claim-document-setups"],
    dependencies=[Depends(require_claim_configuration)],
)


def _validate_identity(body: ClaimDocumentSetupIn) -> None:
    if body.claim_kind == "flex":
        if body.scope_code != SCOPE_STANDARD:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Flexible-benefit document setups use the standard scope.",
            )
    elif body.scope_code not in CLAIM_SCOPE_CODES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f'Unknown claim scope code "{body.scope_code}".',
        )


def _validate_aliases(documents: list[ClaimSetupDocument]) -> None:
    owners: dict[str, str] = {}
    for document in documents:
        for alias in [*document.aliases, document.display]:
            key = " ".join(alias.split()).casefold()
            previous = owners.get(key)
            if previous is not None and previous != document.id:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "ambiguous_claim_document_alias",
                        "message": (
                            f'Alias "{alias}" is used by more than one required '
                            "document in this claim type. Use distinct aliases so "
                            "autofill can place the upload reliably."
                        ),
                    },
                )
            owners[key] = document.id


def _payload(body: ClaimDocumentSetupIn) -> dict[str, Any]:
    _validate_identity(body)
    _validate_aliases(body.documents)
    return {
        "claim_kind": body.claim_kind,
        "claim_key": body.claim_key,
        "scope_code": body.scope_code,
        "display_label": body.display_label,
        "documents": dump_documents(body.documents),
    }


def _available_setups(
    user: CurrentUser, db: Session
) -> list[ClaimDocumentSetupOut]:
    client_id = require_client_id(user)
    options = review_scope_options(user=user, db=db)
    out: list[ClaimDocumentSetupOut] = []
    for claim_type in options.claim_types:
        if claim_type.claim_kind == "flex":
            resolved = resolve_setup(
                db,
                client_id,
                claim_kind="flex",
                claim_key=claim_type.claim_key,
                scope_code=SCOPE_STANDARD,
            )
            out.append(
                ClaimDocumentSetupOut(
                    id=resolved.row.id if resolved.row else None,
                    claim_kind="flex",
                    claim_key=claim_type.claim_key,
                    scope_code=SCOPE_STANDARD,
                    scope_key=claim_scope_key("flex", claim_type.claim_key),
                    product_label="Flexible benefits",
                    display_label=claim_type.display_label,
                    documents=list(resolved.documents),
                    is_default=resolved.row is None,
                    updated_at=resolved.row.updated_at if resolved.row else None,
                )
            )
            continue

        for scope in claim_type.scopes:
            if not scope.configurable:
                continue
            resolved = resolve_setup(
                db,
                client_id,
                claim_kind="insured",
                claim_key=claim_type.claim_key,
                scope_code=scope.scope_code,
                sub_type=scope.sub_type,
            )
            out.append(
                ClaimDocumentSetupOut(
                    id=resolved.row.id if resolved.row else None,
                    claim_kind="insured",
                    claim_key=claim_type.claim_key,
                    scope_code=scope.scope_code,
                    scope_key=claim_scope_key(
                        "insured", claim_type.claim_key, scope.scope_code
                    ),
                    product_label=claim_type.display_label,
                    display_label=scope.display_label,
                    group_code=scope.group_code,
                    group_label=scope.group_label,
                    documents=list(resolved.documents),
                    is_default=resolved.row is None,
                    updated_at=resolved.row.updated_at if resolved.row else None,
                )
            )
    return out


def _target_setup(
    body: ClaimDocumentSetupIn,
    user: CurrentUser,
    db: Session,
) -> ClaimDocumentSetupOut:
    target_key = claim_scope_key(
        body.claim_kind, body.claim_key, body.scope_code
    )
    target = next(
        (
            setup
            for setup in _available_setups(user, db)
            if setup.scope_key == target_key
        ),
        None,
    )
    if target is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "claim_type_not_available",
                "message": (
                    "This claim type is not currently available for the company. "
                    "Refresh the claim settings and choose an available type."
                ),
            },
        )
    return target


def _upsert_claim_document_setup(
    body: ClaimDocumentSetupIn,
    user: CurrentUser,
    db: Session,
) -> str:
    """Write one setup without committing so callers own transaction scope."""
    _target_setup(body, user, db)
    client_id = require_client_id(user)
    data = _payload(body)
    row = find_setup_row(
        db, client_id, body.claim_kind, body.claim_key, body.scope_code
    )
    if row is None:
        row = ClaimDocumentSetup(client_id=client_id, **data)
        db.add(row)
        db.flush()
        write_audit(
            db,
            user,
            "claim_document_setup.created",
            "claim_document_setup",
            row.id,
            after=data,
        )
    else:
        assert_not_stale(
            expected=body.expected_updated_at,
            actual=row.updated_at,
            label="This claim document setup",
        )
        before = {
            "display_label": row.display_label,
            "documents": row.documents,
        }
        row.display_label = data["display_label"]
        row.documents = data["documents"]
        row.updated_at = datetime.now(UTC)
        write_audit(
            db,
            user,
            "claim_document_setup.updated",
            "claim_document_setup",
            row.id,
            before=before,
            after=data,
        )
    db.flush()
    return row.id


@router.get("", response_model=list[ClaimDocumentSetupOut])
def list_claim_document_setups(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimDocumentSetupOut]:
    return _available_setups(user, db)


@router.put("", response_model=ClaimDocumentSetupOut)
def save_claim_document_setup(
    body: ClaimDocumentSetupIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocumentSetupOut:
    _upsert_claim_document_setup(body, user, db)
    db.commit()
    return next(
        setup
        for setup in _available_setups(user, db)
        if setup.scope_key
        == claim_scope_key(body.claim_kind, body.claim_key, body.scope_code)
    )


@router.post("/duplicate", response_model=ClaimDocumentSetupOut)
def duplicate_claim_document_setup(
    body: DuplicateClaimDocumentSetupIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimDocumentSetupOut:
    available = _available_setups(user, db)
    source = next(
        (setup for setup in available if setup.scope_key == body.source_scope_key),
        None,
    )
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source claim type not found")
    copied = [
        document.model_copy(update={"id": uuid4().hex})
        for document in source.documents
    ]
    target = body.target.model_copy(update={"documents": copied})
    target_id = _upsert_claim_document_setup(target, user, db)
    write_audit(
        db,
        user,
        "claim_document_setup.duplicated",
        "claim_document_setup",
        target_id,
        after={
            "source_scope_key": source.scope_key,
            "target_scope_key": claim_scope_key(
                target.claim_kind, target.claim_key, target.scope_code
            ),
        },
    )
    db.commit()
    return next(
        setup
        for setup in _available_setups(user, db)
        if setup.scope_key
        == claim_scope_key(target.claim_kind, target.claim_key, target.scope_code)
    )
