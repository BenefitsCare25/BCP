"""Stage 2 — per-document field extraction via the AI gateway.

One gateway call per stored document; the gateway's cache key is the
document's SHA-256, so a resubmitted receipt never re-extracts. Unreadable
documents don't sink the run — they surface as a ``warning`` rule result the
broker can see.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from anthropic import RateLimitError
from sqlalchemy.orm import Session

from app.core.storage import get_storage
from app.models import Claim, StoredDocument
from app.services import ai_gateway
from app.services.doc_images import DocImageError, vision_blocks_for_document

logger = logging.getLogger(__name__)

# Provider burst-throttle recovery, mirroring the interactive intake path
# (portal_claims._extract_with_throttle_retry). Without it, a transient 429 on
# the 2nd/3rd document of a multi-doc claim propagates out of the whole review
# and dumps the claim back to manual review, discarding the AI spend already
# incurred on earlier documents. This runs in the background review task (sync,
# its own session), so a short blocking sleep is acceptable — and unlike the
# request path we deliberately DON'T roll back here: run_review sets the firm
# search_path once on its session, and releasing the connection mid-run could
# hand back a pool connection scoped to a different (or public) schema.
_THROTTLE_RETRIES = 1
_THROTTLE_BACKOFF_SECONDS = 8.0


def _extract_with_throttle_retry(db: Session, claim: Claim, doc: StoredDocument, blocks):
    """`ai_gateway.extract_claim_document` with one bounded backoff on 429."""
    for attempt in range(_THROTTLE_RETRIES + 1):
        try:
            return ai_gateway.extract_claim_document(
                db,
                client_id=claim.client_id,
                policy_year_id=claim.policy_year_id,
                sha256=doc.sha256,
                blocks=blocks,
                file_name=doc.file_name,
            )
        except RateLimitError:
            if attempt >= _THROTTLE_RETRIES:
                raise
            logger.warning(
                "Claim %s: throttled extracting doc %s — retrying in %ss",
                claim.id, doc.id, _THROTTLE_BACKOFF_SECONDS,
            )
            time.sleep(_THROTTLE_BACKOFF_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


def extract_documents(
    db: Session,
    claim: Claim,
    docs: list[StoredDocument],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract every document's fields.

    Returns ``(extractions, warnings, call_metadata)`` — extraction dicts
    (``{document_id, file_name, sha256, document_type, fields}``), warning
    rule-results for unreadable documents, and per-call gateway metadata for
    token accounting.
    """
    storage = get_storage()
    extractions: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    call_metadata: list[dict[str, Any]] = []

    for doc in docs:
        try:
            raw = storage.read(doc.storage_path)
            blocks = vision_blocks_for_document(raw, Path(doc.storage_path).suffix)
        except (DocImageError, FileNotFoundError, OSError) as exc:
            logger.warning(
                "Claim %s document %s unreadable for AI review: %s",
                claim.id, doc.id, exc,
            )
            warnings.append(
                {
                    "rule": "Every submitted document is machine-readable.",
                    "status": "warning",
                    "source": "deterministic",
                    "evidence": f'"{doc.file_name}" could not be read for AI review.',
                }
            )
            continue

        result = _extract_with_throttle_retry(db, claim, doc, blocks)
        call_metadata.append(result.metadata)
        extractions.append(
            {
                "document_id": doc.id,
                "file_name": doc.file_name,
                "sha256": doc.sha256,
                "document_type": result.document.get("document_type", "unknown"),
                "fields": result.document.get("fields", []),
            }
        )

    # Deterministic guard: if EVERY attached document was unreadable there is
    # zero verified evidence — that must be a hard fail, not a warning the
    # verdict can sail past (the AI required-documents check would otherwise
    # be the only thing standing between "no readable receipt" and
    # `ai_verified`).
    if docs and not extractions:
        warnings.append(
            {
                "rule": "At least one submitted document is machine-readable.",
                "status": "fail",
                "source": "deterministic",
                "evidence": "None of the attached documents could be read for AI review.",
            }
        )

    return extractions, warnings, call_metadata
