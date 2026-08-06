"""Which insurer underwrites a product — resolved PER BENEFIT YEAR.

The insurer is a *placement* fact: it belongs to (company, benefit year,
product), never to the product catalog. Two things make a catalog-level insurer
wrong, and both bit us:

- a firm-library `Product` row (``client_id IS NULL``) is shared by EVERY
  company, so one broker tagging "GPA → Zurich" published that insurer to every
  other company's reports and insurer picker;
- even a company-scoped `Product` row spans every benefit year, so a renewal
  that moves the placement to another insurer silently rewrote last year's
  submissions.

The broker enters it once per year where the placement lives — Company &
Benefits → <product> → Header & Policy → Insurer — which is
``ProductSetup.answers["header"]["insurer"]``: the same field the placement slip
is parsed into and exported from, backed by the insurer catalog dropdown. This
module is the ONE place that reads it.

``Product.insurer`` survives ONLY as a fallback for rows written before the
field moved (no app path writes it any more, and it is no longer editable in
the catalog UI). Never read it directly — a direct read reintroduces both bugs
above for any company whose setup carries the current answer.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PolicyYear, Product, ProductSetup


def _norm(code: str | None) -> str:
    """Product codes are matched case-insensitively — ``ProductSetup`` keys on
    the template code and the catalog on the product code, and the two differ
    only in casing (mirrors ``slip_export.context.answers_by_code``)."""
    return (code or "").strip().upper()


def _captured(answers: dict[str, Any] | None) -> str:
    """The Header & Policy answer — the broker's per-year entry.

    ``answers`` is unvalidated ``dict[str, Any]`` (``SetupSaveIn``), so nothing
    guarantees the shape: guard like ``slip_export.header.captured_answers``
    does. A stored ``{"header": "..."}`` must not 500 the readiness endpoint,
    the insurer listings, the fact-find, the panel cards, the portal claim form
    AND roster matching (which re-syncs underwriting) all at once.
    """
    header = (answers or {}).get("header")
    if not isinstance(header, dict):
        return ""
    value = header.get("insurer")
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    return str(value or "").strip()


def setup_insurers(db: Session, policy_year_id: str) -> dict[str, str]:
    """``{UPPERCASE product code: insurer}`` captured for this benefit year.

    Read from the DRAFT answers, not only confirmed ones: the setup draft is the
    editable source of truth for header values (it is pre-filled by the slip
    parse and autosaves as the broker types), and confirm only materializes
    *structure* — plans, categories, schedules. Waiting for a confirm would
    leave every slip-loaded year reporting no insurer at all.

    A setup with a BLANK answer is still returned (as ``""``). Its presence is
    what makes the answer authoritative over the legacy catalog column — no app
    path can write that column any more, so if a blank answer fell through to
    it, a wrong legacy value would be permanently unclearable.
    """
    return {
        _norm(code): _captured(answers)
        for code, answers in db.execute(
            select(ProductSetup.product_code, ProductSetup.answers).where(
                ProductSetup.policy_year_id == policy_year_id
            )
        )
    }


def insurers_named_in_setups(db: Session, client_id: str | None) -> set[str]:
    """Every insurer name a company's product setups name, across ALL its
    benefit years.

    The insurer catalog's "in use" check has to span this: with the placement's
    insurer living in the setup answers, a catalog-only scan would report a name
    as unused while several years are placed with it, and the delete dialog
    would promise "no existing data changes".
    """
    if not client_id:
        return set()
    rows = db.execute(
        select(ProductSetup.answers)
        .join(PolicyYear, PolicyYear.id == ProductSetup.policy_year_id)
        .where(PolicyYear.client_id == client_id)
    ).scalars()
    return {name for answers in rows if (name := _captured(answers))}


def _legacy(product: Product | None) -> str:
    """The pre-move catalog value, and ONLY when it can mean one company.

    A firm-library row (``client_id IS NULL``) is shared by every company, so
    its legacy tag is never evidence about *this* company's placement — it is
    the leak this module exists to stop. Honouring it as a fallback would have
    kept publishing whichever company's insurer was typed into the shared row
    (in the dev data, one broker's AIA tags were being reported for three other
    companies that had never named an insurer).
    """
    if product is None or product.client_id is None:
        return ""
    return (product.insurer or "").strip()


def insurer_from_answers(
    answers: dict[str, Any] | None, product: Product | None
) -> str:
    """THE resolution rule, for callers that already hold a product's setup
    answers (the slip export loads them for the header wording): the captured
    Header & Policy answer, else the legacy company-scoped catalog value."""
    return _captured(answers) or _legacy(product)


def insurer_map(
    db: Session, policy_year_id: str, products: Iterable[Product]
) -> dict[str, str]:
    """``{product_id: insurer}`` for this year — the setup answer when this year
    has a setup for the code, else the legacy company-scoped catalog value.
    Products with neither are absent, so callers can treat a missing key as "no
    insurer configured" (``.get(pid, "")``)."""
    by_code = setup_insurers(db, policy_year_id)
    out: dict[str, str] = {}
    for product in products:
        code = _norm(product.code)
        # `in`, not `or`: a setup that EXISTS with a blank answer means "no
        # insurer", and must not fall through to the unwritable legacy column.
        name = by_code[code] if code in by_code else _legacy(product)
        if name:
            out[product.id] = name
    return out


def insurer_map_for_ids(
    db: Session, policy_year_id: str, product_ids: Iterable[str]
) -> dict[str, str]:
    """``insurer_map`` for callers holding ids rather than loaded rows."""
    ids = {pid for pid in product_ids if pid}
    if not ids:
        return {}
    products = db.execute(select(Product).where(Product.id.in_(ids))).scalars()
    return insurer_map(db, policy_year_id, products)


