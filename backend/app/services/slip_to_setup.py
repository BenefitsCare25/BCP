"""Map a parsed placement slip onto the guided product-setup form.

The placement-slip parser reverse-engineers a *completed* slip into structured
``ProductSlip`` data (policy header, eligibility categories with rates, and the
Schedule of Benefits per plan). The guided ``ProductSetupForm`` is driven by a
``ProductTemplate`` and its ``SetupAnswers`` JSON.

This module is the bridge: given one ``ProductSlip`` and the matching
``ProductTemplate``, it produces a ``SetupAnswers`` dict so the upload can
pre-fill the form. The broker then reviews/edits and confirms — the slip is the
starting point, not the final word.

Design choices that keep the form's invariants intact:
- The template's ``benefit_items`` stay the canonical, index-aligned structure
  (the SOB grid applies structural edits across every plan by index). We only
  override each item's per-plan *value* from the slip where the item matches by
  number; structure (name/sub-items/properties) is never reshaped from the slip.
- Category ids are derived from slip provenance (sheet + source row) so they are
  stable across re-uploads and don't need a random uuid.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.services.placement_slip_parser import (
    ExtractedCategory,
    ProductSlip,
    normalize_participation,
    split_plan_codes,
)
from app.services.product_templates import ProductTemplate
from app.services.sob_columns import sob_from_plan_items

# PolicyHeader attr -> ordered candidate field ids it can fill. Matched by
# EXACT field id (first existing candidate wins) so a template field whose id
# merely contains "insurer"/"period" (e.g. "reinsurer", "grace_period") can't
# capture the value. Add candidates here when a template uses a different id.
_HEADER_FIELD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("policyholder", ("policyholder",)),
    ("insured", ("insured",)),
    ("address", ("address",)),
    ("business", ("business",)),
    ("period", ("period_of_insurance", "period")),
    ("insurer", ("insurer",)),
    ("policy_no", ("policy_no",)),
    ("admin_basis", ("admin_basis",)),
)
# PolicyHeader attr -> eligibility field id(s) it fills (exact match).
_ELIGIBILITY_FIELD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("eligibility", ("eligibility",)),
    ("eligibility_date", ("eligibility_date",)),
    ("last_entry_age", ("last_entry_age",)),
    ("age_limit_no_underwriting", ("age_limit_no_underwriting",)),
    ("employee_age_limit", ("employee_age_limit",)),
)


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _set_by_exact_id(
    out: dict[str, str], fields: list[Any], candidate_ids: tuple[str, ...], value: str
) -> None:
    field_ids = {f.id for f in fields}
    for cid in candidate_ids:
        if cid in field_ids:
            out[cid] = value
            return


def _header_answers(slip: ProductSlip, tpl: ProductTemplate) -> dict[str, str]:
    ph = slip.policy_header
    out = {f.id: "" for f in tpl.header_fields}
    for attr, candidate_ids in _HEADER_FIELD_HINTS:
        value = _s(getattr(ph, attr, None))
        if value:
            _set_by_exact_id(out, tpl.header_fields, candidate_ids, value)
    return out


def _eligibility_answers(slip: ProductSlip, tpl: ProductTemplate) -> dict[str, str]:
    ph = slip.policy_header
    out = {f.id: "" for f in tpl.eligibility_fields}
    for attr, candidate_ids in _ELIGIBILITY_FIELD_HINTS:
        value = _s(getattr(ph, attr, None))
        if value:
            _set_by_exact_id(out, tpl.eligibility_fields, candidate_ids, value)
    return out


def _participation(slip: ProductSlip) -> str:
    """Most common participation across categories, or "" when the slip is silent.

    We intentionally do NOT default to "compulsory": a blank leaves the form's
    participation dropdown unset so the broker makes an explicit choice, rather
    than silently committing a guess that (for a voluntary product) would drive
    wrong enrollment defaults. Actual slip values are still extracted/normalized
    upstream by the parser.
    """
    # Categories now carry the raw slip cell; normalize to the binary the form's
    # participation dropdown expects before tallying the most common value.
    values = [
        norm
        for c in slip.categories
        if (norm := normalize_participation(_s(c.participation)))
    ]
    if values:
        return Counter(values).most_common(1)[0][0]
    return ""


def _cover_description(slip: ProductSlip) -> str:
    for plan in slip.plans:
        if _s(plan.cover_description):
            return _s(plan.cover_description)
    return ""


def _referenced_plan_codes(slip: ProductSlip) -> set[str]:
    codes = {_s(c.plan_code) for c in slip.categories if _s(c.plan_code)}
    codes |= {_s(p.code) for p in slip.plans if _s(p.code)}
    return {c for c in codes if c}


def _source_labels_by_plan(slip: ProductSlip) -> dict[str, str]:
    """plan code -> the slip's verbatim SOB column header for it.

    Populated only for per-plan-column layouts; a descriptive single-schedule
    sheet contributes nothing, so those products keep their generic column
    labels ("All plans") instead of a meaningless "Schedule of Benefits".
    """
    return {
        _s(p.code): _s(p.source_label)
        for p in slip.plans
        if _s(p.code) and _s(p.source_label)
    }


def _norm_key(key: str) -> str:
    """Normalize a sub-item key for matching, so the parser's "(a)" lines up with
    a template's "a"."""
    return _s(key).strip("()").strip().lower()


def _limits(limits: Any) -> list[dict[str, str | None]]:
    return [
        {"label": _s(lim.label), "value": _s(lim.value) or None}
        for lim in (limits or [])
        if _s(lim.label)
    ]


def _slip_values_by_plan(slip: ProductSlip) -> dict[str, dict[str, dict[str, Any]]]:
    """plan code -> {benefit number -> overlay} from the parsed Schedule of Benefits.

    The overlay carries everything a flat value drops: the value, its footnote,
    qualifier limits, and per-sub-item values/notes/limits keyed by normalized
    sub-key. Consumed by ``_plan_answers`` to pre-fill the setup form.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for plan in slip.plans:
        by_number: dict[str, dict[str, Any]] = {}
        for item in plan.items:
            num = _s(item.number)
            if not num:
                continue
            subs = {
                (_norm_key(s.key) or _s(s.name).lower()): {
                    "key": _s(s.key),
                    "name": _s(s.name),
                    "value": _s(s.value),
                    "note": _s(s.note) or None,
                    "limits": _limits(s.limits),
                }
                for s in item.sub_items
            }
            by_number[num] = {
                "name": _s(item.name),
                "value": _s(item.value),
                "note": _s(item.note) or None,
                "limits": _limits(item.limits),
                "properties": dict(item.properties or {}),
                "subs": subs,
            }
        if by_number:
            out[_s(plan.code)] = by_number
    return out


def _sub_answer(sub: Any, overlay: dict[str, Any] | None) -> dict[str, Any]:
    """One template sub-item + its slip overlay (value/note/limits), if matched.

    The slip overlay wins; absent one, fall back to any default the file
    template carries on the sub-item (carrier-standard schedules like GBT)."""
    ov = overlay or {}
    tpl_value = getattr(sub, "value", "") or None
    tpl_note = getattr(sub, "note", None)
    return {
        "key": sub.key,
        "name": sub.name,
        "kind": getattr(sub, "kind", "amount"),
        "value": ov.get("value") or tpl_value,
        "note": ov.get("note") or tpl_note,
        "limits": ov.get("limits", []),
    }


_COPAY_PROP_PREFIXES = ("per_visit", "co_payment", "per_policy_year")


def _is_copay_props(props: dict[str, Any]) -> bool:
    return any(str(k).startswith(_COPAY_PROP_PREFIXES) for k in props)


def _overlay_for(
    overrides: dict[str, dict[str, Any]], bi: Any
) -> tuple[dict[str, Any], str | None]:
    """The slip overlay for a template benefit item, plus the slip number that
    supplied it (so extras can exclude consumed numbers).

    Matched by exact number first. Copay template rows additionally accept the
    slip's dash-group number ("-1 Panel" fills template row "1") — outpatient
    sheets enumerate the per-visit/co-payment groups with dash keys while the
    template numbers them plainly.
    """
    ov = overrides.get(bi.number)
    if ov is not None:
        return ov, bi.number
    if getattr(bi, "kind", "") == "copay":
        dash = f"-{bi.number}"
        ov = overrides.get(dash)
        if ov is not None:
            return ov, dash
    return {}, None


def _infer_extra_kind(ov: dict[str, Any]) -> str:
    """Editor kind for a slip-extracted line the template doesn't declare."""
    if _is_copay_props(ov.get("properties") or {}):
        return "copay"
    if _s(ov.get("value")).upper() in {"YES", "NO", "NA"}:
        return "boolean"
    return "amount"


def _extra_item_numbers(
    slip_values: dict[str, dict[str, dict[str, Any]]],
    template_plan_codes: list[str],
    consumed: set[str],
) -> list[str]:
    """Slip benefit numbers with no template row, first-seen order.

    These become appended form lines so a schedule richer than the template
    (CDL GCGP's TCM / A&E / Overseas GP / WhiteCoat groups) isn't silently
    truncated to the template's static list. Only plans the template actually
    knows contribute — a slip on a foreign coding scheme adds nothing.
    """
    extras: list[str] = []
    for code in template_plan_codes:
        for num in slip_values.get(code, {}):
            if num not in consumed and num not in extras:
                extras.append(num)
    return extras


def _plan_answers(slip: ProductSlip, tpl: ProductTemplate) -> list[dict[str, Any]]:
    referenced = _referenced_plan_codes(slip)
    slip_values = _slip_values_by_plan(slip)
    source_labels = _source_labels_by_plan(slip)
    # Only drive selection from the slip when at least one referenced code
    # actually matches a template plan; otherwise the slip used a different
    # coding scheme and we'd deselect everything, blocking confirm — so fall
    # back to the template's default selection.
    use_referenced = bool(referenced) and any(
        tp.code in referenced for tp in tpl.plans
    )

    # Which slip numbers the template rows consume (union across plans), so the
    # remainder can be appended as extra lines with the same structure per plan.
    consumed: set[str] = set()
    for tp in tpl.plans:
        overrides = slip_values.get(tp.code, {})
        for bi in tpl.benefit_items:
            _, used = _overlay_for(overrides, bi)
            if used is not None:
                consumed.add(used)
    extra_numbers = _extra_item_numbers(
        slip_values, [tp.code for tp in tpl.plans], consumed
    )
    # Structural fields (name/kind/sub skeleton) of an extra line come from the
    # first plan that carries it, so the row set is identical across plans.
    extra_shape: dict[str, dict[str, Any]] = {}
    for code in (tp.code for tp in tpl.plans):
        for num, ov in slip_values.get(code, {}).items():
            if num in extra_numbers and num not in extra_shape:
                extra_shape[num] = ov

    plans: list[dict[str, Any]] = []
    for tp in tpl.plans:
        selected = (tp.code in referenced) if use_referenced else tp.default_selected
        overrides = slip_values.get(tp.code, {})
        items: list[dict[str, Any]] = []
        for bi in tpl.benefit_items:
            ov, _ = _overlay_for(overrides, bi)
            # Slip overlay wins; absent one, fall back to any default the file
            # template carries (carrier-standard schedules like GBT that the slip
            # references but doesn't reproduce). Stays the baseline either way.
            value = ov.get("value") or (getattr(bi, "value", "") or "")
            sub_ov = ov.get("subs") or {}
            items.append(
                {
                    "number": bi.number,
                    "name": bi.name,
                    "kind": bi.kind,
                    "value": value,
                    # The value the line loads with (slip, template default, or
                    # blank) is its own baseline, so a fresh pre-fill isn't
                    # mis-flagged as "edited".
                    "default_value": value,
                    "note": ov.get("note") or (getattr(bi, "note", None)),
                    "limits": ov.get("limits", []),
                    "properties": dict(ov.get("properties") or {}),
                    "sub_items": [
                        _sub_answer(s, sub_ov.get(_norm_key(s.key)))
                        for s in bi.sub_items
                    ],
                }
            )
        for num in extra_numbers:
            shape = extra_shape[num]
            ov = overrides.get(num) or {}
            value = _s(ov.get("value"))
            items.append(
                {
                    "number": num,
                    "name": _s(shape.get("name")),
                    "kind": _infer_extra_kind(shape),
                    "value": value,
                    "default_value": value,
                    "note": ov.get("note") or shape.get("note"),
                    "limits": ov.get("limits", []),
                    "properties": dict(ov.get("properties") or {}),
                    # Row skeleton from the shared shape (identical across
                    # plans); each plan's own overlay supplies its values.
                    "sub_items": [
                        {
                            "key": _s(shape_sub.get("key")),
                            "name": _s(shape_sub.get("name")),
                            "kind": "amount",
                            "value": _s((ov.get("subs") or {}).get(sk, {}).get("value")),
                            "note": (ov.get("subs") or {}).get(sk, {}).get("note")
                            or shape_sub.get("note"),
                            "limits": (ov.get("subs") or {}).get(sk, {}).get(
                                "limits", []
                            ),
                        }
                        for sk, shape_sub in (shape.get("subs") or {}).items()
                    ],
                }
            )
        plans.append(
            {
                "code": tp.code,
                "label": tp.label,
                "selected": selected,
                # Verbatim slip header (absent for descriptive layouts and for
                # manually-built drafts); the SOB column label prefers it.
                "source_label": source_labels.get(tp.code) or None,
                "benefit_items": items,
            }
        )
    return plans


# Synthetic single-rate key (matches the frontend RateTableSection FLAT_TIER and
# the materializer's _FLAT_RATE_KEY) — used for per-member and per-$1,000-SI
# products whose rate isn't split across EO/ES/EC/EF tiers.
_FLAT_RATE_KEY = "flat"


def _rate_table(slip: ProductSlip) -> dict[str, Any]:
    """plan code -> {tier|flat -> {rate, premium}} from the parsed Rate section.

    Tiered medical keeps a cell per EO/ES/EC/EF tier; per-member and
    per-$1,000-SI products collapse to a single ``flat`` cell carrying the
    member rate / rate-per-1,000 and the annual premium.
    """
    table: dict[str, dict[str, dict[str, float]]] = {}
    for cat in slip.categories:
        # A category's plan_code may be a composite/annotated code
        # ("1 / International", "1A/1B", "1 - Employees"). Different templates key
        # plans differently: hand-authored file templates use the split lead codes
        # ("1", "1A"/"1B"), while a slip-synthesized template keys plans on the
        # *full* composite ("1 / International"). Index the cells under both the
        # split codes and the raw composite so the lookup hits regardless —
        # otherwise the rate lands under a key no plan looks up and the form (and
        # confirm-time materialization) renders rate/premium as 0.
        raw_code = _s(cat.plan_code)
        plan_codes = split_plan_codes(raw_code)
        if not plan_codes:
            continue
        if raw_code and raw_code not in plan_codes:
            plan_codes = [*plan_codes, raw_code]
        if cat.rate_tiers:
            cells = {
                str(tier): {
                    "rate": float(cell.get("rate") or 0),
                    "premium": float(cell.get("premium") or 0),
                }
                for tier, cell in cat.rate_tiers.items()
            }
        elif cat.premium_rate is not None or cat.annual_premium is not None:
            # An earnings-based (statutory/WICA) rate is a % of payroll, NOT a
            # per-member dollar rate — storing it as `rate` would let the
            # per_member materializer compute rate x headcount (nonsense). Keep
            # the parsed annual premium but drop the rate (review finding #7).
            is_earnings = cat.rate_basis == "earnings_based"
            cells = {
                _FLAT_RATE_KEY: {
                    "rate": 0.0 if is_earnings else float(cat.premium_rate or 0),
                    "premium": float(cat.annual_premium or 0),
                }
            }
        else:
            continue
        for code in plan_codes:
            table.setdefault(code, {}).update(cells)
    return table


def _category_id(slip: ProductSlip, cat: ExtractedCategory) -> str:
    return f"{slip.sheet}_row_{cat.source_row}"


def _category_rows(slip: ProductSlip, tpl: ProductTemplate) -> list[dict[str, Any]]:
    tier_codes = [t.code for t in tpl.tiers]
    # Sum insured + basis are only meaningful for sum-assured products; for
    # per-member/tiered products the slip's SI (if any) isn't shown and must not
    # be silently carried into the draft (review finding #9).
    is_sum_assured = tpl.basis_model == "sum_assured"
    rows: list[dict[str, Any]] = []
    for cat in slip.categories:
        # Tier columns take the slip's own per-tier split when its count column
        # was divided by tier ("* Number" over EO/ES/EC/EF); a slip stating one
        # undivided count leaves them at zero for the broker to apportion, since
        # a single total can't be split without inventing figures. Per-member /
        # sum-assured products carry the single headcount, sum insured and basis
        # straight through from the slip.
        parsed_tiers = cat.tier_counts or {}
        rows.append(
            {
                "id": _category_id(slip, cat),
                "insured": _s(cat.insured),
                "category": _s(cat.category),
                "participation": _s(cat.participation),
                "plan_code": _s(cat.plan_code),
                "tiers": {t: int(parsed_tiers.get(t, 0)) for t in tier_codes},
                "num_employees": cat.num_employees or 0,
                "sum_insured": cat.sum_insured if is_sum_assured else None,
                "basis": (_s(cat.basis) or None) if is_sum_assured else None,
            }
        )
    return rows


def build_setup_answers(slip: ProductSlip, tpl: ProductTemplate) -> dict[str, Any]:
    """Project a parsed ``ProductSlip`` onto the ``SetupAnswers`` form shape."""
    plans = _plan_answers(slip, tpl)
    # De-dupe the per-plan SOB grid into the decoupled column model: a life/CI
    # slip with many sum-insured tiers collapses to one "All plans" column, while
    # GHS keeps its genuinely-distinct columns. Plans are reduced to stubs
    # (selection + label); the grid now lives once in ``sob``.
    sob = sob_from_plan_items(plans)
    plan_stubs = [
        {
            "code": p["code"],
            "label": p["label"],
            "selected": p["selected"],
            "source_label": p.get("source_label"),
        }
        for p in plans
    ]
    return {
        "header": _header_answers(slip, tpl),
        "eligibility": _eligibility_answers(slip, tpl),
        "participation": _participation(slip),
        "cover_description": _cover_description(slip),
        "plans": plan_stubs,
        "sob": sob,
        "rate_table": _rate_table(slip),
        "categories": _category_rows(slip, tpl),
        "arrangements": {
            a.id: a.default_enabled for a in tpl.additional_arrangements
        },
    }
