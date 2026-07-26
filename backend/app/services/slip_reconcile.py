"""Reconcile a parsed placement slip so it is internally consistent.

The raw parser (`placement_slip_parser`) extracts categories and plans
independently, and real slips link them in three messy ways:

* **Descriptive / sum-assured products** (GTL/GPA/term-life/PA): category cells
  name plans ("Plan A: Hay Job Grade 16…") so categories reference codes A/B/C/D,
  but those plans share ONE benefit schedule the SOB extractor collapses into a
  single plan.
* **Composite plan headers**: one Schedule-of-Benefits column is headed with
  several codes that share it — "1A/1B", "B1 & B", "1/U01/U04/U06" — while the
  categories cite the individual codes.
* **Empty linkage**: categories carry no plan code but exactly one plan exists.

In every case the fix is the same: *for each plan code a category references,
emit a concrete ``Plan`` row carrying the covering schedule*, so every category
resolves to a real plan (no dangling links → no blank Schedule of Benefits).

The deterministic parser stays pure; reconciliation is a separate, independently
tested step, so existing parser tests are unaffected. The per-product diagnostics
this returns drive the upload response, the review UI, and the AI fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.services.placement_slip_parser import (
    _KNOWN_PRODUCT_CODES,
    ExtractedPlan,
    PlacementSlip,
    ProductSlip,
    split_plan_codes,
)

# Below this confidence the upload surfaces the product for review / AI fallback.
LOW_CONFIDENCE = 0.6


@dataclass(frozen=True)
class ProductDiagnostics:
    """Per-product summary of how a sheet parsed + was reconciled."""

    sheet: str
    product_code: str
    layout: str  # "per_plan" | "descriptive" | "none"
    rate_model: str | None
    n_categories: int
    n_plans: int
    n_benefit_items: int  # total Schedule-of-Benefits lines parsed across plans
    confidence: float  # 0..1
    reconciliation: str  # consistent | fan_out | assign_default | unmappable | no_plans
    issues: list[str] = field(default_factory=list)
    low_confidence: bool = False
    needs_attention: bool = False  # review queue / AI-fallback candidate
    empty_sob: bool = False  # plans parsed but their Schedule of Benefits is empty
    used_ai: bool = False  # AI extraction produced/augmented this product
    # Template-memory: stable signature of this SOB layout + the column->role
    # mapping used, so a broker can correct it and have the fix reused.
    fingerprint: str | None = None
    column_roles: dict | None = None
    # Registry classification: which layout family extracted the sheet and
    # whether the product code was recognized (registry entry or a stored
    # broker classification). Unknown codes need the broker to pick a product
    # type instead of trusting the generic default.
    layout_family: str | None = None
    registry_known: bool = True
    needs_classification: bool = False


@dataclass(frozen=True)
class ReconciledSlip:
    slip: PlacementSlip
    diagnostics: list[ProductDiagnostics]


def _rate_model(product: ProductSlip) -> str | None:
    """Most common non-null rate_basis across the product's categories."""
    counts: dict[str, int] = {}
    for c in product.categories:
        if c.rate_basis:
            counts[c.rate_basis] = counts.get(c.rate_basis, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def _product_recognized(product_code: str) -> bool:
    code = product_code.upper()
    if code in _KNOWN_PRODUCT_CODES:
        return True
    return code.split("-", 1)[0] in _KNOWN_PRODUCT_CODES  # GHS-LOCALS etc.


def _confidence(recognized: bool, reconciliation: str) -> float:
    score = 1.0
    if not recognized:
        score -= 0.35
    score -= {
        "consistent": 0.0,
        "assign_default": 0.05,
        "fan_out": 0.05,
        "no_plans": 0.25,
        "unmappable": 0.35,
    }.get(reconciliation, 0.0)
    return max(0.0, min(1.0, round(score, 2)))


def _fanned_display_name(ref: str) -> str:
    """Display name for a plan split out of a composite/descriptive schedule.

    Codes are usually bare ("U01" -> "Plan U01"), but some slips carry the word
    in the code itself (VDL GBT's "Plan A - International / Asia"), which the
    old unconditional prefix turned into "Plan Plan A - International / Asia".
    """
    return ref if ref.strip().lower().startswith("plan") else f"Plan {ref}"


def _reconcile_product(product: ProductSlip) -> tuple[ProductSlip, ProductDiagnostics]:
    cats = product.categories
    plans = product.plans

    # Distinct referenced codes, first-seen order. Dedup is case-SENSITIVE so two
    # categories citing the same plan in different casing ("A" and "a") each get a
    # Plan row keyed to their own casing — otherwise the second would dangle, since
    # hydration looks up (product_id, plan_code) by exact string.
    referenced: list[str] = []
    seen: set[str] = set()
    for c in cats:
        code = (c.plan_code or "").strip()
        if code and code not in seen:
            seen.add(code)
            referenced.append(code)

    # token (lowercased) -> covering plan, via whole code + each composite token.
    token_to_plan: dict[str, ExtractedPlan] = {}
    for p in plans:
        token_to_plan.setdefault(p.code.strip().lower(), p)
        for tok in split_plan_codes(p.code):
            token_to_plan.setdefault(tok.lower(), p)

    consumed: set[int] = set()
    emitted: list[ExtractedPlan] = []
    uncovered: list[str] = []
    fanned = False
    for ref in referenced:
        cover = token_to_plan.get(ref.lower())
        if cover is None and len(plans) == 1:
            cover = plans[0]  # descriptive single-schedule product
        if cover is None:
            uncovered.append(ref)
            continue
        consumed.add(id(cover))
        if cover.code.strip() != ref:  # split a composite / fanned a descriptive plan
            fanned = True
            # Keep the slip header only when it actually NAMES this code. A
            # composite header does ("PLAN 1/U01/U04/U06" -> U01), but a
            # descriptive single schedule fanned across unrelated codes does
            # not: CBRE's GMM has one "Plan 3" header covering categories
            # 1A/1B/2A/…, and labelling that column "Plan 3" would claim a
            # coverage split the slip never made.
            header_codes = {t.lower() for t in split_plan_codes(cover.code)}
            emitted.append(
                replace(
                    cover,
                    code=ref,
                    display_name=_fanned_display_name(ref),
                    source_label=(
                        cover.source_label if ref.lower() in header_codes else None
                    ),
                )
            )
        else:
            emitted.append(cover)

    # Preserve any parsed plans that were never referenced (don't lose schedules).
    kept = [p for p in plans if id(p) not in consumed]
    new_plans = tuple(emitted + kept)

    # Assign the sole plan's code to categories that carry none.
    assigned_default = False
    if len(plans) == 1 and not referenced:
        cats = tuple(replace(c, plan_code=plans[0].code) for c in cats)
        assigned_default = True

    issues: list[str] = []
    if not plans:
        reconciliation = "no_plans"
        layout = "none"
        issues.append(
            f"Categories reference plan(s) {referenced} but no Schedule of Benefits "
            "was parsed." if referenced else "No Schedule of Benefits parsed."
        )
    else:
        layout = "per_plan" if len(plans) > 1 else "descriptive"
        if uncovered:
            reconciliation = "unmappable"
            issues.append(
                f"Categories reference plan(s) {uncovered} with no matching schedule "
                f"(available: {sorted({p.code for p in plans})})."
            )
        elif fanned:
            reconciliation = "fan_out"
        elif assigned_default:
            reconciliation = "assign_default"
        else:
            reconciliation = "consistent"

    recognized = _product_recognized(product.product_code)
    if not recognized:
        issues.append(f"Sheet product code {product.product_code!r} not recognized.")

    # SOB-extraction signal, independent of plan-code mapping. A Schedule-of-
    # Benefits SECTION was located on the sheet (so a fingerprint exists) but
    # extraction produced zero benefit lines — the GBT-style failure where the
    # column layout went unrecognized. Keying off the fingerprint (not plan
    # count) is what makes this reachable: the parser drops zero-item plans, so a
    # mis-read SOB yields no plans, yet the section — and its fingerprint — is
    # still present and the column mapping is correctable.
    n_items = sum(len(p.items) for p in new_plans)
    empty_sob = product.sob_fingerprint is not None and n_items == 0
    if empty_sob:
        issues.append(
            "A Schedule of Benefits was found but no benefit lines were "
            "extracted — the SOB column layout may be unrecognized. "
            "Use 'Fix column mapping' to correct it."
        )

    confidence = _confidence(recognized, reconciliation)
    if empty_sob:
        confidence = max(0.0, round(confidence - 0.3, 2))
    low_conf = confidence < LOW_CONFIDENCE

    # The parse path stamps registry_known=False on codes neither the registry
    # nor a stored broker classification recognizes; those need the broker to
    # pick a product type (hand-built ProductSlips default to known).
    needs_classification = not product.registry_known
    if needs_classification:
        issues.append(
            f"Product type for {product.product_code!r} is unclassified — "
            "extracted with the generic profile. Classify it so the right "
            "form and rate model apply."
        )

    diag = ProductDiagnostics(
        sheet=product.sheet,
        product_code=product.product_code,
        layout=layout,
        rate_model=_rate_model(product),
        n_categories=len(cats),
        n_plans=len(new_plans),
        n_benefit_items=n_items,
        confidence=confidence,
        reconciliation=reconciliation,
        issues=issues,
        low_confidence=low_conf,
        needs_attention=low_conf
        or empty_sob
        or needs_classification
        or reconciliation in {"no_plans", "unmappable"},
        empty_sob=empty_sob,
        fingerprint=product.sob_fingerprint,
        column_roles=product.sob_roles,
        layout_family=product.layout_family,
        registry_known=product.registry_known,
        needs_classification=needs_classification,
    )
    return replace(product, categories=cats, plans=new_plans), diag


def reconcile_slip(slip: PlacementSlip) -> ReconciledSlip:
    """Return a consistency-reconciled copy of ``slip`` plus per-product diagnostics."""
    products = []
    diagnostics = []
    for product in slip.products:
        reconciled, diag = _reconcile_product(product)
        products.append(reconciled)
        diagnostics.append(diag)
    return ReconciledSlip(slip=replace(slip, products=tuple(products)), diagnostics=diagnostics)
