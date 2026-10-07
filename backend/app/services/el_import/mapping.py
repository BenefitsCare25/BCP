"""Suggest how an Employee Listing's blocks and wording map onto the company setup.

Two decisions are the broker's, and both are only *suggested* here:

* **Block -> products.** A listing block ("GROUP HOSPITAL & SURGICAL (GHS)")
  reports every policy of that product type: GAS's GHS block holds the main
  policy's plans and the separate directors' policy (``GHS-50011774``).
* **Listing wording -> slip category.** The listing writes its own labels
  ("2) All Professionals, Executives & Manangement Staff - Plan 1 (1 Bed
  Restr)") for the slip's categories ("All Professionals, Executives &
  Management Staff", plan 1). A reviewed mapping is remembered per company and
  reused on the next upload, keyed by the label's normalized wording.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Category, Product
from app.services import product_registry
from app.services.el_workbook import ElWorkbook
from app.services.el_workbook.values import text
from app.services.eligibility_mapping import category_signature

_NUMBERING = re.compile(r"^\s*\(?\d+[.)]\s*")
_BASIS_TAIL = re.compile(
    r"\s+-\s+(?:\d+\s*x\b.*|\$?\s*[\d,]+(?:\.\d+)?\s*$|s\$.*)", re.IGNORECASE
)
_PLAN_IN_LABEL = re.compile(r"\bplan\s*([0-9]+[a-z]?)\b", re.IGNORECASE)
_LEADING_CODE = re.compile(r"^\s*([0-9]+[a-z]?)\b")
_WORD = re.compile(r"[a-z0-9]+")
NOT_COVERED = "__not_covered__"


def normalize_label(label: str) -> str:
    return " ".join(_WORD.findall(text(label).lower()))


def _cohort_words(label: str) -> str:
    """The cohort part of a listing label: numbering, basis and plan dropped."""
    core = _NUMBERING.sub("", text(label))
    core = _BASIS_TAIL.sub("", core)
    core = re.split(r"\s+-\s+plan\b|\s+plan\s+\d", core, flags=re.IGNORECASE)[0]
    core = re.sub(r"\([^)]*\b(bed|ward|pte|restr|plan)\b[^)]*\)", "", core, flags=re.I)
    return " ".join(_WORD.findall(core.lower()))


def plan_hint(label: str, plan_value: Any) -> str | None:
    plan_text = text(plan_value)
    if (m := _LEADING_CODE.match(plan_text)) is not None:
        return m.group(1).upper()
    if (m := _PLAN_IN_LABEL.search(text(label))) is not None:
        return m.group(1).upper()
    return None


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb)
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


@dataclass
class LabelStats:
    label: str
    employees: int = 0
    dependants: int = 0
    plans: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class CategoryOption:
    product_code: str
    category_id: str
    category_label: str
    plan_code: str | None
    signature: str


@dataclass
class LabelSuggestion:
    block: int
    key: str  # normalized listing label
    stats: LabelStats
    choice: CategoryOption | None
    not_covered: bool
    confidence: float
    source: str  # "saved" | "suggested" | "none"
    options: list[CategoryOption]


def block_label_stats(workbook: ElWorkbook) -> dict[int, dict[str, LabelStats]]:
    """Distinct category wording per product block, with headcounts."""
    out: dict[int, dict[str, LabelStats]] = {}
    for emp in workbook.employees:
        people = [(emp.covers, False), *((d.covers, True) for d in emp.dependants)]
        for covers, is_dependant in people:
            for cover in covers:
                label = text(cover.values.get("category"))
                if not label:
                    continue
                stats = out.setdefault(cover.block, {}).setdefault(
                    normalize_label(label), LabelStats(label)
                )
                if is_dependant:
                    stats.dependants += 1
                else:
                    stats.employees += 1
                if (plan := text(cover.values.get("plan_type"))):
                    stats.plans[plan] += 1
    return out


def company_products(db: Session, client_id: str, policy_year_id: str) -> list[Product]:
    """Products the company is set up with this year (catalog or its own rows)."""
    in_use = select(Category.product_id).where(Category.policy_year_id == policy_year_id)
    return list(
        db.execute(
            select(Product).where(
                tenant_or_global(Product.client_id, client_id), Product.id.in_(in_use)
            )
        ).scalars()
    )


def suggest_block_products(
    workbook: ElWorkbook, products: list[Product]
) -> dict[int, list[str]]:
    """Products each listing block reports: same product type as the block's
    code hint, or the same kind of cover (GD -> DENTAL)."""
    out: dict[int, list[str]] = {}
    for index, block in enumerate(workbook.layout.blocks):
        if block.kind != "product" or not block.code_hint:
            continue
        hint = product_registry.get_entry(block.code_hint)
        base = product_registry.base_code(block.code_hint)
        same_type = [p.code for p in products if product_registry.base_code(p.code) == base]
        if not same_type and hint is not None:
            same_type = [
                p.code
                for p in products
                if (entry := product_registry.get_entry(product_registry.base_code(p.code)))
                and entry.care_route == hint.care_route
                and entry.form_profile == hint.form_profile
            ]
        out[index] = sorted(same_type)
    return out


def category_options(
    db: Session, policy_year_id: str, products: list[Product], codes: list[str]
) -> list[CategoryOption]:
    by_id = {p.id: p.code for p in products if p.code in codes}
    if not by_id:
        return []
    rows = db.execute(
        select(Category).where(
            Category.policy_year_id == policy_year_id,
            Category.product_id.in_(list(by_id)),
        ).order_by(Category.priority)
    ).scalars()
    return [
        CategoryOption(
            product_code=by_id[c.product_id],
            category_id=c.id,
            category_label=c.display_name,
            plan_code=str((c.plan_assignments or {}).get("plan_code") or "") or None,
            signature=category_signature(c.raw_description or c.display_name),
        )
        for c in rows
        if c.product_id is not None
    ]


def _resolve_saved(entry: dict[str, Any], options: list[CategoryOption]) -> CategoryOption | None:
    for option in options:
        if (
            option.product_code == entry.get("product_code")
            and option.signature == entry.get("category_signature")
            and (entry.get("plan_code") in (None, "", option.plan_code))
        ):
            return option
    return None


def suggest_labels(
    stats_by_block: dict[int, dict[str, LabelStats]],
    options_by_block: dict[int, list[CategoryOption]],
    saved: dict[str, Any],
) -> list[LabelSuggestion]:
    out: list[LabelSuggestion] = []
    for block, labels in sorted(stats_by_block.items()):
        options = options_by_block.get(block, [])
        saved_block = saved.get(str(block)) or {}
        for key, stats in labels.items():
            entry = saved_block.get(key)
            if isinstance(entry, dict) and entry.get(NOT_COVERED):
                out.append(LabelSuggestion(block, key, stats, None, True, 1.0, "saved", options))
                continue
            if isinstance(entry, dict) and (hit := _resolve_saved(entry, options)):
                out.append(LabelSuggestion(block, key, stats, hit, False, 1.0, "saved", options))
                continue
            cohort = _cohort_words(stats.label)
            plan = plan_hint(stats.label, stats.plans.most_common(1)[0][0] if stats.plans else "")
            scored = sorted(
                (
                    (
                        _similarity(cohort, _cohort_words(o.category_label))
                        + (0.15 if plan and o.plan_code and plan == o.plan_code.upper() else 0.0),
                        o,
                    )
                    for o in options
                ),
                key=lambda pair: pair[0],
            )
            best = scored[-1] if scored else None
            choice = best[1] if best and best[0] >= 0.55 else None
            out.append(LabelSuggestion(
                block, key, stats, choice, False,
                round(min(best[0], 1.0), 2) if best else 0.0,
                "suggested" if choice else "none", options,
            ))
    return out
