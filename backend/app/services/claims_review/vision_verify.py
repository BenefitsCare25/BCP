"""Stage 4 — selective vision re-checks of comparison concerns.

Up to ``MAX_VISION_CHECKS`` gateway calls per claim, spent on
MISMATCH/UNCERTAIN comparisons whose field map opts into vision. Each concern
is checked against the documents in order until one CONFIRMS the claimed
value; a CONFIRMED verdict flips a MISMATCH/UNCERTAIN comparison to MATCH
(the claimed value IS in the document — the text comparison missed it).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.models import Claim, StoredDocument
from app.services import ai_gateway
from app.services.claims_review.field_maps import VISION_FIELDS
from app.services.doc_images import DocImageError, vision_blocks_for_document

logger = logging.getLogger(__name__)

MAX_VISION_CHECKS = 4

# MISSING_IN_PDF (claim states a value, no document showed it) is verifiable
# too: a value the text pass missed may still be legible to vision. If vision
# CONFIRMS, the comparison flips to MATCH; if it can't, the field stays
# MISSING_IN_PDF and the verdict flags it (see verdict.py) — evidence for a
# vision-checked field is never assumed present just because confidence is high.
_VERIFIABLE_STATUSES = frozenset({"MISMATCH", "UNCERTAIN", "MISSING_IN_PDF"})
_VISION_FIELDS = VISION_FIELDS

# MISMATCH/UNCERTAIN comparisons get the shared MAX_VISION_CHECKS budget FIRST:
# a vision recheck can flip a false MISMATCH back to MATCH (clearing a flag),
# whereas a MISSING_IN_PDF that can't be confirmed only stays flagged either
# way. Checking missing-value fields last keeps them from starving the
# mismatch rechecks (which is what a limited budget must protect).
_STATUS_PRIORITY = {"MISMATCH": 0, "UNCERTAIN": 0, "MISSING_IN_PDF": 1}


def _question(comparison: dict[str, Any]) -> str:
    field = comparison.get("field_name")
    value = comparison.get("claim_value")
    return (
        f'The claim states {field} = "{value}". Does this document show that '
        "value, or a semantically equivalent one (different formatting, "
        "currency symbols, date format, abbreviations)?"
    )


def run_vision_checks(
    db: Session,
    claim: Claim,
    docs: list[StoredDocument],
    field_comparisons: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns ``(updated_comparisons, vision_checks, call_metadata)``."""
    storage = get_storage()
    updated = [dict(c) for c in field_comparisons]
    vision_checks: list[dict[str, Any]] = []
    call_metadata: list[dict[str, Any]] = []
    calls = 0

    # Stable sort so mismatch/uncertain rechecks claim the budget before
    # missing-value rechecks; mutations still land on the `updated` dicts.
    ordered = sorted(updated, key=lambda c: _STATUS_PRIORITY.get(c.get("status"), 0))
    for comparison in ordered:
        if calls >= MAX_VISION_CHECKS:
            break
        if comparison.get("status") not in _VERIFIABLE_STATUSES:
            continue
        if comparison.get("field_name") not in _VISION_FIELDS:
            continue
        if comparison.get("claim_value") in (None, ""):
            continue

        question = _question(comparison)
        for doc in docs:
            if calls >= MAX_VISION_CHECKS:
                break
            try:
                raw = storage.read(doc.storage_path)
                blocks = vision_blocks_for_document(raw, Path(doc.storage_path).suffix)
            except (DocImageError, FileNotFoundError, OSError) as exc:
                logger.warning(
                    "Claim %s: skipping vision check on unreadable doc %s: %s",
                    claim.id, doc.id, exc,
                )
                continue

            result = ai_gateway.verify_claim_concern(
                db,
                client_id=claim.client_id,
                policy_year_id=claim.policy_year_id,
                claim_id=claim.id,
                question=question,
                doc_sha256=doc.sha256,
                blocks=blocks,
            )
            calls += 1
            call_metadata.append(result.metadata)
            vision_checks.append(
                {
                    "field_name": comparison.get("field_name"),
                    "question": question,
                    "document_id": doc.id,
                    "file_name": doc.file_name,
                    "verdict": result.verdict,
                    "explanation": result.explanation,
                }
            )
            if result.verdict == "CONFIRMED":
                comparison["status"] = "MATCH"
                comparison["vision_verified"] = True
                comparison["notes"] = (
                    f"Vision-verified in \"{doc.file_name}\": {result.explanation}"
                )
                break
            if result.verdict == "REFUTED":
                comparison["notes"] = (
                    f"Vision re-check confirmed the discrepancy: {result.explanation}"
                )
                break
            # UNCERTAIN → try the next document (if any budget remains).

    return updated, vision_checks, call_metadata
