"""Financial reconciliation over AI-extracted claim-document fields.

AI reads each document. This module performs the money arithmetic in ordinary
Python so totals are reproducible, auditable, and never delegated to a model.
The chosen/excluded decision is stamped into ``ClaimAIReview.extractions`` so a
completed review retains the exact breakdown that drove its verdict.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.models import Claim
from app.services.claim_intake_suggest import document_financial_reading

_CENT = Decimal("0.01")
_AMOUNT_RULE = "AI-extracted invoice/receipt amounts reconcile to the claim."


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _best(indices: list[int], rows: list[dict[str, Any]]) -> int:
    return max(indices, key=lambda i: float(rows[i]["financial"].get("confidence") or 0.0))


def _document_priority(row: dict[str, Any]) -> int:
    document_type = str(row.get("document_type") or "").casefold()
    if "itemised" in document_type or "itemized" in document_type or "summary" in document_type:
        return 1
    if "final" in document_type or "receipt" in document_type or "tax invoice" in document_type:
        return 3
    if "invoice" in document_type or "bill" in document_type:
        return 2
    return 0


def _preferred_pool(
    by_amount: dict[Decimal, list[int]], rows: list[dict[str, Any]]
) -> list[int] | None:
    if len(by_amount) == 1:
        return next(iter(by_amount.values()))
    indices = [index for peers in by_amount.values() for index in peers]
    highest = max((_document_priority(rows[index]) for index in indices), default=0)
    preferred = [index for index in indices if _document_priority(rows[index]) == highest]
    preferred_amounts = {
        _decimal(rows[index]["financial"].get("amount")) for index in preferred
    }
    return preferred if len(preferred_amounts) == 1 else None


def _include(
    rows: list[dict[str, Any]],
    chosen: int,
    peers: list[int],
    *,
    duplicate_note: str,
) -> None:
    for index in peers:
        financial = rows[index]["financial"]
        if index == chosen:
            financial.update(
                included_in_total=True,
                resolution="included",
                note="Included in the document total.",
            )
        else:
            financial.update(
                resolution="duplicate",
                note=duplicate_note,
            )


def _mark_ambiguous(rows: list[dict[str, Any]], indices: list[int], note: str) -> None:
    for index in indices:
        rows[index]["financial"].update(resolution="ambiguous", note=note)


def reconcile_document_amounts(
    claim: Claim,
    extractions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annotate extraction snapshots and return the verdict-driving rule result."""
    rows = deepcopy(extractions)
    claimed = _decimal(claim.amount_claimed) or Decimal("0.00")
    claim_currency = str(claim.currency or "SGD").upper()
    candidates: list[int] = []

    for index, extraction in enumerate(rows):
        fields = [field for field in extraction.get("fields", []) if isinstance(field, dict)]
        reading = document_financial_reading(fields)
        amount = _decimal(reading.get("amount"))
        extraction["financial"] = {
            "invoice_number": reading.get("invoice_number"),
            "invoice_key": reading.get("invoice_key"),
            "amount": float(amount) if amount is not None else None,
            # An amount without an explicit printed currency is interpreted in
            # the claim's declared currency, which is itself compared by AI.
            "currency": str(reading.get("currency") or claim_currency).upper(),
            "confidence": float(reading.get("confidence") or 0.0),
            "included_in_total": False,
            "resolution": "no_amount",
            "note": "No positive invoice or receipt total was read from this document.",
        }
        if amount is not None:
            candidates.append(index)

    if not candidates:
        return rows, {
            "rule": _AMOUNT_RULE,
            "status": "fail",
            "source": "deterministic",
            "severity": "critical",
            "evidence": "No positive invoice or receipt amount was available to total.",
        }

    numbered: dict[str, list[int]] = defaultdict(list)
    unnumbered: list[int] = []
    for index in candidates:
        key = rows[index]["financial"].get("invoice_key")
        (numbered[str(key)] if key else unnumbered).append(index)

    ambiguous = False
    included: list[int] = []
    for indices in numbered.values():
        by_amount: dict[Decimal, list[int]] = defaultdict(list)
        for index in indices:
            amount = _decimal(rows[index]["financial"].get("amount"))
            if amount is not None:
                by_amount[amount].append(index)
        # A final invoice and itemised statement can share a number but print
        # net and gross figures. Prefer the authoritative document family; if
        # that still leaves conflicting values, a broker must decide. Never use
        # the member-entered claim amount to select its own supporting evidence.
        chosen_pool = _preferred_pool(by_amount, rows)
        if chosen_pool is None:
            ambiguous = True
            _mark_ambiguous(
                rows,
                indices,
                "Documents with this invoice number show different totals; "
                "review which one is claimable.",
            )
            continue
        chosen = _best(chosen_pool, rows)
        _include(
            rows,
            chosen,
            indices,
            duplicate_note=(
                "Not added again because another document carries the same invoice number."
            ),
        )
        included.append(chosen)

    known_total = sum(
        (
            _decimal(rows[index]["financial"].get("amount")) or Decimal("0.00")
            for index in included
            if rows[index]["financial"].get("currency") == claim_currency
        ),
        Decimal("0.00"),
    )
    known_currencies = {
        rows[index]["financial"].get("currency") for index in included
    }

    if unnumbered:
        if not numbered and len(unnumbered) == 1:
            chosen = unnumbered[0]
            _include(
                rows,
                chosen,
                unnumbered,
                duplicate_note="Not added again because this is the selected document total.",
            )
            included.append(chosen)
        elif (
            numbered
            and known_currencies <= {claim_currency}
            and known_total.quantize(_CENT) == claimed
        ):
            for index in unnumbered:
                rows[index]["financial"].update(
                    resolution="supporting",
                    note=(
                        "Supporting document amount is not added because the numbered "
                        "invoices already match the claim."
                    ),
                )
        else:
            ambiguous = True
            _mark_ambiguous(
                rows,
                unnumbered,
                "The document has no invoice number, so it cannot be safely grouped as "
                "a separate bill or duplicate. Review it manually.",
            )

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for index in included:
        financial = rows[index]["financial"]
        amount = _decimal(financial.get("amount"))
        if amount is not None:
            totals[str(financial["currency"])] += amount

    if ambiguous:
        rule = {
            "rule": _AMOUNT_RULE,
            "status": "fail",
            "source": "deterministic",
            "severity": "critical",
            "evidence": (
                "AI read document amounts, but the billing total is ambiguous and needs "
                "manual review."
            ),
        }
    elif set(totals) != {claim_currency}:
        currencies = ", ".join(sorted(totals)) or "none"
        rule = {
            "rule": _AMOUNT_RULE,
            "status": "fail",
            "source": "deterministic",
            "severity": "critical",
            "evidence": (
                "Document amounts could not be reconciled in the claim currency "
                f"{claim_currency}; extracted currencies: {currencies}."
            ),
        }
    else:
        document_total = totals[claim_currency].quantize(_CENT)
        difference = (document_total - claimed).quantize(_CENT)
        matches = difference == Decimal("0.00")
        rule = {
            "rule": _AMOUNT_RULE,
            "status": "pass" if matches else "fail",
            "source": "deterministic",
            "severity": "critical" if not matches else "info",
            "evidence": (
                f"Document total {claim_currency} {_money(document_total)} "
                f"{'matches' if matches else 'does not match'} the claimed "
                f"{claim_currency} {_money(claimed)}"
                + ("." if matches else f" (difference {claim_currency} {_money(difference)}).")
            ),
        }
    return rows, rule


def amount_breakdown(
    claim: Claim,
    extractions: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Build the typed API projection, including compatibility for older runs."""
    if not extractions:
        return None
    rows = deepcopy(extractions)
    if not all(isinstance(row.get("financial"), dict) for row in rows):
        rows, _ = reconcile_document_amounts(claim, rows)

    claimed = _decimal(claim.amount_claimed) or Decimal("0.00")
    claim_currency = str(claim.currency or "SGD").upper()
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    lines: list[dict[str, Any]] = []
    ambiguous = False
    readable = False
    for row in rows:
        financial = row.get("financial") or {}
        amount = _decimal(financial.get("amount"))
        included = bool(financial.get("included_in_total"))
        currency = str(financial.get("currency") or claim_currency).upper()
        resolution = str(financial.get("resolution") or "no_amount")
        readable = readable or amount is not None
        ambiguous = ambiguous or resolution == "ambiguous"
        if included and amount is not None:
            totals[currency] += amount
        lines.append(
            {
                "document_id": str(row.get("document_id") or ""),
                "file_name": str(row.get("file_name") or "document"),
                "document_type": str(row.get("document_type") or "unknown"),
                "invoice_number": financial.get("invoice_number"),
                "amount": float(amount) if amount is not None else None,
                "currency": currency if amount is not None else None,
                "confidence": float(financial.get("confidence") or 0.0),
                "included_in_total": included,
                "resolution": resolution,
                "note": str(financial.get("note") or "Review this document amount."),
            }
        )

    difference: Decimal | None = None
    if not readable:
        status = "not_available"
        note = (
            "The AI could not read a positive invoice or receipt total. "
            "Review the documents manually."
        )
    elif ambiguous or set(totals) != {claim_currency}:
        status = "needs_review"
        note = (
            "The extracted amounts cannot be combined safely. "
            "Review the excluded or ambiguous rows."
        )
    else:
        difference = (totals[claim_currency] - claimed).quantize(_CENT)
        status = "match" if difference == Decimal("0.00") else "mismatch"
        note = (
            "The document total matches the amount claimed."
            if status == "match"
            else "The document total differs from the amount claimed. Review before deciding."
        )
    return {
        "status": status,
        "claimed_amount": float(claimed),
        "claimed_currency": claim_currency,
        "totals": [
            {"currency": currency, "amount": float(total.quantize(_CENT))}
            for currency, total in sorted(totals.items())
        ],
        "difference": float(difference) if difference is not None else None,
        "lines": lines,
        "note": note,
    }
