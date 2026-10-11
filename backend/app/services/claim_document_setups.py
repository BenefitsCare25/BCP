"""Claim-type-scoped required documents and recognition definitions.

Each exact member claim choice owns an independent ordered document set. The
same display name in two claim types is deliberately two different records.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha1
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, ClaimDocumentSetup
from app.schemas.claims import (
    ClaimDocKeyField,
    ClaimSetupDocument,
    CoverageOptionsOut,
)
from app.services.claim_doc_types import (
    DocTypeDefinition,
    KeyField,
    definition_targets_scope,
    resolve_doc_types,
)
from app.services.claim_intake import (
    DOC_SLOT_LABELS,
    GHS_SECTOR_SCOPE_CODES,
    HOSPITALISATION_SLOTS_BY_SECTOR,
    SCOPE_GHS_HOSPITALISATION_GOVT,
    SCOPE_GHS_HOSPITALISATION_PRIVATE,
    SCOPE_STANDARD,
    claim_scope_key,
    generic_scope_code,
    required_doc_slots,
    sector_scope_codes,
)
from app.services.claims_review.field_maps import SLOT_DOC_FAMILIES
from app.services.sg_hospitals import SECTOR_GOVT, SECTOR_PRIVATE


@dataclass(frozen=True)
class ResolvedDocumentSetup:
    documents: tuple[ClaimSetupDocument, ...]
    row: ClaimDocumentSetup | None


_DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "invoice_receipt": ("invoice", "receipt", "tax invoice"),
    "sp_invoice": ("specialist invoice", "clinic invoice", "hospital invoice"),
    "finalised_tax_invoice": (
        "tax invoice (finalised)",
        "finalised tax invoice",
        "hospital bill",
    ),
    "summary_tax_invoice": (
        "summary tax invoice",
        "summary bill",
        "hospital bill summary",
    ),
    "itemised_tax_invoice": (
        "itemised tax invoice",
        "itemized tax invoice",
        "detailed hospital bill",
    ),
    "discharge_summary": (
        "discharge summary",
        "after visit summary",
        "clinical discharge summary",
        "endoscopy report",
    ),
}


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def setup_rows(db: Session, client_id: str) -> list[ClaimDocumentSetup]:
    return list(
        db.execute(
            select(ClaimDocumentSetup)
            .where(ClaimDocumentSetup.client_id == client_id)
            .order_by(
                ClaimDocumentSetup.claim_kind,
                ClaimDocumentSetup.claim_key,
                ClaimDocumentSetup.scope_code,
            )
        ).scalars()
    )


def find_setup_row(
    db: Session,
    client_id: str,
    claim_kind: str,
    claim_key: str,
    scope_code: str,
) -> ClaimDocumentSetup | None:
    wanted_key = _norm(claim_key)
    wanted_scope = _norm(scope_code) or SCOPE_STANDARD
    return next(
        (
            row
            for row in setup_rows(db, client_id)
            if row.claim_kind == claim_kind
            and _norm(row.claim_key) == wanted_key
            and _norm(row.scope_code) == wanted_scope
        ),
        None,
    )


def read_documents(raw: Any) -> tuple[ClaimSetupDocument, ...]:
    """Defensive JSON reader: one malformed entry never hides the setup."""
    out: list[ClaimSetupDocument] = []
    seen_keys: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        try:
            document = ClaimSetupDocument.model_validate(entry)
        except (ValidationError, TypeError):
            continue
        if document.key in seen_keys:
            continue
        seen_keys.add(document.key)
        out.append(document)
    return tuple(out[:15])


def dump_documents(
    documents: list[ClaimSetupDocument] | tuple[ClaimSetupDocument, ...],
) -> list[dict[str, Any]]:
    return [document.model_dump(exclude_none=True) for document in documents]


def _default_slot_keys(
    claim_kind: str,
    claim_key: str,
    scope_code: str,
    sub_type: str | None,
    provider_name: str | None,
) -> list[str]:
    if scope_code == SCOPE_GHS_HOSPITALISATION_GOVT:
        return list(HOSPITALISATION_SLOTS_BY_SECTOR[SECTOR_GOVT])
    if scope_code == SCOPE_GHS_HOSPITALISATION_PRIVATE:
        return list(HOSPITALISATION_SLOTS_BY_SECTOR[SECTOR_PRIVATE])
    return required_doc_slots(
        claim_key if claim_kind == "insured" else None,
        sub_type,
        provider_name,
        claim_kind=claim_kind,
    )


def _legacy_required_documents(
    db: Session,
    client_id: str,
    claim_kind: str,
    claim_key: str,
    scope_code: str,
) -> tuple[str, ...]:
    """Resolve the old review-rule extras while a setup is still unsaved.

    They become visible member requirements in the new editor and are folded
    into the authoritative scoped setup on first save. Once a setup row exists
    it replaces this compatibility bridge completely.
    """
    from app.services.claim_review_configs import find_config_row

    candidates = [scope_code]
    if scope_code in GHS_SECTOR_SCOPE_CODES:
        candidates.append(generic_scope_code(scope_code) or "*")
    candidates.append("*")
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        row = find_config_row(
            db, client_id, claim_kind, claim_key, candidate
        )
        if row is not None and row.enabled:
            return tuple(
                value.strip()
                for value in (row.required_documents or [])
                if value and value.strip()
            )
    return ()


def _legacy_document(value: str) -> ClaimSetupDocument:
    digest = sha1(value.casefold().encode("utf-8")).hexdigest()[:8]
    # ASCII only: `ClaimSetupDocument.key` is `^[a-z0-9_]+$`, and str.isalnum
    # accepts accented and CJK letters; the digest keeps such names unique.
    ascii_name = (
        unicodedata.normalize("NFKD", value.casefold())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    stem = "_".join(re.findall(r"[a-z0-9]+", ascii_name))[:16]
    return ClaimSetupDocument(
        id=f"legacy:{digest}",
        key=f"legacy_{stem or 'document'}_{digest}"[:32],
        # Review rules accepted 200-character names; setup names cap at 128.
        display=value[:128],
        instructions=f"Attach the {value.casefold()}.",
        aliases=[value[:128]],
        key_fields=[],
    )


def default_documents(
    db: Session,
    client_id: str,
    *,
    claim_kind: str,
    claim_key: str,
    scope_code: str,
    sub_type: str | None = None,
    provider_name: str | None = None,
) -> tuple[ClaimSetupDocument, ...]:
    scope_key = claim_scope_key(claim_kind, claim_key, scope_code)
    legacy = resolve_doc_types(db, client_id)
    out: list[ClaimSetupDocument] = []
    for slot_key in _default_slot_keys(
        claim_kind, claim_key, scope_code, sub_type, provider_name
    ):
        matched = next(
            (
                definition
                for definition in legacy
                if definition.slot_key == slot_key
                and (
                    not definition.claim_scope_keys
                    or definition_targets_scope(definition, scope_key)
                )
            ),
            None,
        )
        label = DOC_SLOT_LABELS.get(slot_key, slot_key.replace("_", " ").title())
        aliases = (
            list(matched.aliases)
            if matched is not None and matched.aliases
            else list(_DEFAULT_ALIASES.get(slot_key, (label.casefold(),)))
        )
        fields = (
            [
                ClaimDocKeyField(
                    name=field.name,
                    keywords=list(field.tokens),
                    optional=field.optional,
                )
                for field in matched.key_fields
            ]
            if matched is not None
            else []
        )
        out.append(
            ClaimSetupDocument(
                id=f"default:{slot_key}",
                key=slot_key,
                display=label,
                instructions=f"Attach the {label.casefold()}.",
                aliases=aliases,
                key_fields=fields,
            )
        )
    existing_names = {
        _norm(name)
        for document in out
        for name in (
            document.display,
            SLOT_DOC_FAMILIES.get(document.key, document.display),
        )
    }
    for legacy_name in _legacy_required_documents(
        db, client_id, claim_kind, claim_key, scope_code
    ):
        if _norm(legacy_name) in existing_names:
            continue
        out.append(_legacy_document(legacy_name))
        existing_names.add(_norm(legacy_name))
        if len(out) >= 15:
            break
    return tuple(out)


def resolve_setup(
    db: Session,
    client_id: str,
    *,
    claim_kind: str,
    claim_key: str,
    scope_code: str,
    sub_type: str | None = None,
    provider_name: str | None = None,
) -> ResolvedDocumentSetup:
    row = find_setup_row(db, client_id, claim_kind, claim_key, scope_code)
    if row is not None:
        return ResolvedDocumentSetup(read_documents(row.documents), row)
    return ResolvedDocumentSetup(
        default_documents(
            db,
            client_id,
            claim_kind=claim_kind,
            claim_key=claim_key,
            scope_code=scope_code,
            sub_type=sub_type,
            provider_name=provider_name,
        ),
        None,
    )


def current_setup_for_claim(db: Session, claim: Claim) -> ResolvedDocumentSetup:
    """Resolve the claim's current identity without consulting its snapshot."""
    from app.services.claim_review_configs import claim_scope_for

    kind, key, scope = claim_scope_for(claim)
    return resolve_setup(
        db,
        claim.client_id,
        claim_kind=kind,
        claim_key=key,
        scope_code=scope,
        sub_type=claim.sub_type,
        provider_name=claim.provider_name,
    )


def setup_for_claim(db: Session, claim: Claim) -> ResolvedDocumentSetup:
    if claim.required_documents_snapshot is not None:
        return ResolvedDocumentSetup(
            read_documents(claim.required_documents_snapshot), None
        )
    return current_setup_for_claim(db, claim)


def slot_keys_for_claim(db: Session, claim: Claim) -> list[str]:
    return [document.key for document in setup_for_claim(db, claim).documents]


def _sector_for_scope(scope_code: str) -> str | None:
    if scope_code.endswith("_govt"):
        return SECTOR_GOVT
    if scope_code.endswith("_private"):
        return SECTOR_PRIVATE
    return None


def definitions_from_documents(
    documents: tuple[ClaimSetupDocument, ...],
    *,
    scope_key: str,
    scope_code: str,
) -> tuple[DocTypeDefinition, ...]:
    sector = _sector_for_scope(scope_code)
    return tuple(
        DocTypeDefinition(
            key=f"{scope_key}:{document.key}",
            display=document.display,
            aliases=tuple(
                dict.fromkeys(
                    [
                        *(alias.casefold() for alias in document.aliases),
                        document.display.casefold(),
                    ]
                )
            ),
            key_fields=tuple(
                KeyField(
                    field.name,
                    tuple(token.casefold() for token in field.keywords)
                    or (field.name.casefold(),),
                    optional=bool(field.optional),
                )
                for field in document.key_fields
            ),
            sector=sector,
            slot_key=document.key,
            claim_scope_keys=(scope_key,),
        )
        for document in documents
    )


def definitions_for_claim(db: Session, claim: Claim) -> tuple[DocTypeDefinition, ...]:
    from app.services.claim_review_configs import claim_scope_for

    _kind, _key, scope = claim_scope_for(claim)
    return definitions_from_documents(
        setup_for_claim(db, claim).documents,
        scope_key=claim_scope_key(claim.claim_kind, _key, scope),
        scope_code=scope,
    )


def _coverage_scopes(
    coverage: CoverageOptionsOut,
) -> list[tuple[str, str, str, str | None]]:
    """(kind, key, scope code, sub-type) for every claim choice the member holds,
    with each GHS subclaim expanded to its Government and Private leaves."""
    scopes: list[tuple[str, str, str, str | None]] = []
    for option in coverage.insured:
        for claim_type in option.claim_types:
            code = claim_type.scope_code or SCOPE_STANDARD
            scopes.extend(
                ("insured", option.product_code, leaf, claim_type.sub_type)
                for leaf in sector_scope_codes(code) or (code,)
            )
    if coverage.flex is not None:
        scopes.extend(
            ("flex", category.name, SCOPE_STANDARD, None)
            for category in coverage.flex.categories
        )
    return scopes


def configured_definitions(
    db: Session, client_id: str, coverage: CoverageOptionsOut
) -> tuple[DocTypeDefinition, ...]:
    """Pre-selection intake recognition over the member's own claim choices.

    Each choice contributes its effective document set — the saved setup, or
    the defaults an unsaved choice still serves — so a partial save never drops
    recognition, and generic titles shared by several choices stay ambiguous
    instead of routing to whichever saved row sorts first. If the company has
    not saved any new-style setup yet, retain the legacy registry so the
    rollout is behaviour-preserving.
    """
    if not setup_rows(db, client_id):
        return resolve_doc_types(db, client_id)
    out: list[DocTypeDefinition] = []
    for kind, key, scope_code, sub_type in _coverage_scopes(coverage):
        documents = resolve_setup(
            db,
            client_id,
            claim_kind=kind,
            claim_key=key,
            scope_code=scope_code,
            sub_type=sub_type,
        ).documents
        out.extend(
            definitions_from_documents(
                documents,
                scope_key=claim_scope_key(kind, key, scope_code),
                scope_code=scope_code,
            )
        )
    return tuple(out)
