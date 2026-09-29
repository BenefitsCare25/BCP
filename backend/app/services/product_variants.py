"""Traits a product VARIANT takes from its base product type.

A variant ("GHS-VTS") is a second, separately placed policy of a type the client
already buys. Whether it is created by a slip upload or by hand in the Add
Product dialog, it must behave like its base — same participation model,
outpatient flag and dependant cover — so both paths resolve those here.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Product
from app.models.product import ParticipationModel
from app.services import product_registry
from app.services.insurance_lines import infer_line


def _base_row(db: Session, client_id: str | None, base: str) -> Product | None:
    """The base product row: the client's own wins over the firm catalog's."""
    rows = db.execute(
        select(Product).where(
            func.upper(Product.code) == base.upper(),
            tenant_or_global(Product.client_id, client_id),
        )
    ).scalars()
    best: Product | None = None
    for row in rows:
        if best is None or row.client_id is not None:
            best = row
    return best


def variant_traits(
    db: Session, client_id: str | None, base: str, label: str
) -> dict[str, Any]:
    """Column values for a new variant ``Product`` of ``base`` labelled ``label``.

    Returns ``display_name``, ``participation_model``, ``has_dependants``,
    ``is_outpatient`` and the ``product_metadata`` that marks the row a variant.
    """
    template = _base_row(db, client_id, base)
    entry = product_registry.get_entry(base)
    base_name = (
        template.display_name if template is not None
        else entry.name if entry is not None else base
    )
    return {
        "display_name": (f"{base_name} ({label})" if label else base_name)[:255],
        "participation_model": (
            template.participation_model if template is not None
            else ParticipationModel.standard
        ),
        "has_dependants": bool(
            (template is not None and template.has_dependants)
            or (entry is not None and entry.has_dependants)
        ),
        "is_outpatient": bool(template is not None and template.is_outpatient),
        "product_metadata": {
            "line": infer_line(base),
            "base_code": base,
            "variant_label": label,
        },
    }
