"""Rate-section extraction and category enrichment.

Tier vocabulary is registry-driven: composite family tiers (EO/ES/EC/EF)
canonicalize onto the keys persisted in ``plan_assignments.rate_tiers``, while
dependant-only schemes (SO/CO/FO/SC on dependants sheets, Spouse/Child rate
columns) keep their own canonical keys — they price dependant cover standalone
and must never be folded onto ES/EC/EF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.services import product_registry
from app.services.excel_reader import Cell
from app.services.slip_parsing.models import ExtractedCategory
from app.services.slip_parsing.text import (
    _PLAN_INLINE,
    _currency_amount,
    _int_code,
    _non_empty,
    _norm,
    _row_text,
    _safe_float,
    split_plan_codes,
)

# source header token → canonical tier key, across every registered scheme.
_TIER_TOKEN_TO_CANON: dict[str, str] = {
    token: canon
    for scheme in product_registry.TIER_SCHEMES.values()
    for token, canon in scheme.token_map.items()
}
# Canonical keys that price dependant cover standalone (never employee tiers).
DEPENDANT_TIER_KEYS: frozenset[str] = frozenset(
    canon
    for scheme in product_registry.TIER_SCHEMES.values()
    if scheme.member_scope == "dependant"
    for canon in scheme.token_map.values()
)

_TIER_NAMES = tuple(_TIER_TOKEN_TO_CANON)
# A tier name as a standalone token (word boundary) — matches "EO", "EO Premium",
# "EO / ES" but not the letters inside "EMPLOYEES".
_TIER_TOKEN = re.compile(
    r"\b(?:" + "|".join(sorted(_TIER_TOKEN_TO_CANON, key=len, reverse=True)) + r")\b"
)


@dataclass(frozen=True)
class _RateRow:
    """Intermediate rate data keyed by category text or plan code."""
    key: str  # normalized category text or plan code
    rate_basis: str
    rate: float | None = None
    sum_insured: float | None = None
    annual_premium: float | None = None
    rate_tiers: dict[str, dict[str, float]] | None = None
    insured: str = ""  # owning entity — disambiguates multi-entity rate tables
    # True only for per-member rate rows keyed on a Plan column ("1A/1B",
    # "1 - Employees"); enables compound-key token expansion at enrich time
    # WITHOUT polluting category-text-keyed flat/per_1000/earnings tables.
    expand_tokens: bool = False
    # Member-type of a per-member rate row from its key suffix: "employee",
    # "dependent", "both" (one rate covers employee + dependants), or None.
    member_type: str | None = None
    # Statutory (WICA): estimated annual earnings the premium is rated on.
    estimated_annual_earnings: float | None = None
    # Full text of an annotated premium cell whose amount was parsed out
    # (GBT's "$3,169.80 (Subject to Minimum Policy Premium of S$500)").
    premium_note: str | None = None


def _tier_suffix_keys(key: str) -> list[str]:
    """Canonical tier keys named by a per-member rate key's suffix.

    "2 - EO" → ["EO"]; "2 - SO/CO" → ["SO", "CO"]; [] when the suffix isn't
    pure tier vocabulary ("1 - Employees", "1 / International"). Dental-style
    slips split one plan's rate rows by member tier this way — the suffix names
    WHO the rate prices, not another plan.
    """
    m = re.match(r"^\s*[^\s-]+\s*-\s*(.+?)\s*$", key or "")
    if not m:
        return []
    out: list[str] = []
    for part in re.split(r"[/&,+]", m.group(1)):
        canon = _TIER_TOKEN_TO_CANON.get(part.strip().upper())
        if canon is None:
            return []
        out.append(canon)
    return out


def _member_type(key: str) -> str | None:
    """Classify a per-member rate row's member-type from its key suffix
    ("1 - Employees" / "1 - Dependents" / "2 - Employees / Dependents", or the
    tier-token form "2 - EO" / "2 - SO/CO"). Returns "employee", "dependent",
    "both" (a single rate covering both), or None."""
    low = (key or "").lower()
    has_emp = "employ" in low
    has_dep = "depend" in low
    if has_emp and has_dep:
        return "both"
    if has_dep:
        return "dependent"
    if has_emp:
        return "employee"
    tiers = _tier_suffix_keys(key)
    if tiers:
        if all(t in DEPENDANT_TIER_KEYS for t in tiers):
            return "dependent"
        if tiers == ["EO"]:
            return "employee"
        return "both"  # family tiers (ES/EC/EF) price employee + dependants
    return None


def _rate_section_ended(row: list[Cell]) -> bool:
    """True when a row marks the end of a Rate section (the grand-total or a
    following section), as opposed to merely containing the column label
    'Annual Premium'.

    Multi-entity slips (e.g. WICA's two insured companies) repeat the
    column-header row, which contains the words "Annual Premium". Keying the
    stop condition on the *first cell* avoids halting at that repeated header
    and dropping the second entity's rates.
    """
    col0 = _norm(row[0]).lower() if row and _non_empty(row[0]) else ""
    if not col0:
        return False
    return (
        col0.startswith("annual premium")
        or col0.startswith("experience refund")
        or col0.startswith("maximum limit")
        or col0.startswith("cover")
        or col0.startswith("schedule of benefits")
    )


def _is_rate_header_row(row: list[Cell]) -> bool:
    """True for a repeated Insured/Category column-header row inside a Rate
    section (skipped, not treated as data)."""
    cells = [_norm(c).lower() for c in row if _non_empty(c)]
    return "category" in cells and ("insured" in cells or "plan" in cells)


def _read_insured(row: list[Cell], insured_col: int) -> str | None:
    """Read the insured-entity cell, ignoring the literal header 'Insured'."""
    if 0 <= insured_col < len(row) and _non_empty(row[insured_col]):
        val = _norm(row[insured_col])
        if val.lower() != "insured":
            return val
    return None


def _find_rate_section_start(rows: list[list[Cell]]) -> int:
    for i, row in enumerate(rows):
        text = _row_text(row or []).strip()
        if re.match(r"rate\s*:", text, re.IGNORECASE):
            return i
    return -1


def _parse_age_band(text: str) -> tuple[int | None, int | None] | None:
    """``(min, max)`` for an age-band label, or None when it isn't one.

    Handles the shapes life-product voluntary rate tables use:
    ``"35 to 44"`` / ``"35 - 44"`` → (35, 44); ``"34 years old & below"`` /
    ``"below 35"`` → (None, 34); ``"65 & above"`` → (65, None). A trailing
    parenthetical (``"70 to 74 (renewal only)"``) is ignored.
    """
    t = re.sub(r"\(.*?\)", " ", text.lower())
    m = re.search(r"(\d+)\s*(?:to|[-–—])\s*(\d+)", t)  # noqa: RUF001 — en/em dash ranges
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # "N & below" is INCLUSIVE of N (≤N); "below/under N" is EXCLUSIVE (<N ⇒ ≤N-1).
    m = re.search(r"(\d+)\s*(?:years?\s*old\s*)?(?:&|and)\s*below", t)
    if m:
        return (None, int(m.group(1)))
    m = re.search(r"(?:below|under)\s*(\d+)", t)
    if m:
        return (None, int(m.group(1)) - 1)
    # Symmetrically: "N & above" is INCLUSIVE (≥N); "above/over N" is EXCLUSIVE (>N).
    m = re.search(r"(\d+)\s*(?:years?\s*old\s*)?(?:&|and)\s*above", t)
    if m:
        return (int(m.group(1)), None)
    m = re.search(r"(?:above|over)\s*(\d+)", t)
    if m:
        return (int(m.group(1)) + 1, None)
    return None


def _extract_voluntary_rates(rows: list[list[Cell]]) -> tuple[dict[str, Any], ...]:
    """Parse a 'Voluntary Rates / Based on Age Last Birthday' table into ordered
    age bands carrying a per-S$1000 rate.

    Detection-driven: any product publishing such a table gets it (life GTL/GCI
    in practice). Voluntary tiers — employee up/downgrades AND dependants —
    price off this table instead of the flat compulsory rate. Returns () when
    the table is absent (GPA's voluntary cover is flat; most medical products
    have no such table).
    """
    start = -1
    for i, row in enumerate(rows):
        if "voluntary rate" in _row_text(row or []).lower():
            start = i
            break
    if start < 0:
        return ()

    bands: list[dict[str, Any]] = []
    for i in range(start + 1, min(len(rows), start + 20)):
        label: str | None = None
        rate: float | None = None
        for c in (rows[i] or []):
            if isinstance(c, bool) or c is None:
                continue
            if isinstance(c, (int, float)):
                # The rate is the RIGHTMOST numeric — take the last, so a leading
                # numeric column (a member count, an age value) isn't mistaken for it.
                rate = float(c)
            elif label is None and _parse_age_band(str(c)) is not None:
                label = str(c)
        if label is None or rate is None:
            if bands:  # a non-data row after the table ends the table
                break
            continue
        span = _parse_age_band(label)
        if span is None:
            continue
        bands.append({"label": _norm(label), "min": span[0], "max": span[1], "rate": rate})
    return tuple(bands)


def _tier_cells(row: list[Cell]) -> dict[str, int]:
    """Canonical tier key → column for cells that ARE a tier token (exact,
    case-insensitive). Used to find the tier header row and its columns."""
    out: dict[str, int] = {}
    for c, val in enumerate(row or []):
        if val is None:
            continue
        s = str(val).strip().upper()
        canon = _TIER_TOKEN_TO_CANON.get(s)
        if canon is not None and canon not in out:
            out[canon] = c
    return out


def _tier_labels(row: list[Cell]) -> dict[str, str]:
    """Canonical tier key → the slip's own label, for non-identity tokens
    ("Spouse" → SO). Lets the UI keep the client's vocabulary."""
    out: dict[str, str] = {}
    for val in row or []:
        if val is None:
            continue
        s = str(val).strip()
        canon = _TIER_TOKEN_TO_CANON.get(s.upper())
        if canon is not None and s.upper() != canon:
            out.setdefault(canon, s)
    return out


def _find_tier_header_row(rows: list[list[Cell]], rate_start: int) -> int:
    """The row carrying the tier column headers, at or just below "Rate :".

    Most slips put the tier tokens on the "Rate :" row itself (CDL GHS, OSI);
    others print "Rate :" alone and the tier tokens one row down (VDL). The
    anchor row matches on a single standalone token (parity with the original
    detector, which tolerated merged cells like "EO Premium"); the rows below
    require ≥2 exact tier-token cells so a data row containing a stray token
    can't masquerade as the header.
    """
    header = rows[rate_start] if rate_start < len(rows) else []
    if any(c is not None and _TIER_TOKEN.search(str(c).upper()) for c in header or []):
        return rate_start
    for idx in (rate_start + 1, rate_start + 2):
        if idx < len(rows) and len(_tier_cells(rows[idx])) >= 2:
            return idx
    return -1


def extract_rate_section(
    rows: list[list[Cell]],
) -> tuple[list[_RateRow], dict[str, str] | None]:
    """Extract the Rate section: the rate rows plus any non-standard tier
    labels the sheet used (e.g. {"SO": "Spouse"})."""
    rate_start = _find_rate_section_start(rows)
    if rate_start < 0:
        return [], None

    header_row = rows[rate_start] if rate_start < len(rows) else []
    header_text = " ".join(str(c) for c in header_row if c).lower()

    # Tiered vs per-$1,000-SI format. The tier header row may sit on the
    # "Rate :" row or 1-2 rows below it (VDL prints "Rate :" alone).
    tier_row = _find_tier_header_row(rows, rate_start)
    is_per_1000 = "1,000" in header_text or "1000" in header_text

    if tier_row >= 0:
        labels = _tier_labels(rows[tier_row]) or None
        return _parse_tiered_rates(rows, tier_row), labels
    return _parse_flat_rates(rows, rate_start, is_per_1000), None


def _extract_rate_data(rows: list[list[Cell]]) -> list[_RateRow]:
    """Extract premium rate data from the Rate section of a product sheet."""
    return extract_rate_section(rows)[0]


def _parse_tiered_rates(rows: list[list[Cell]], tier_row: int) -> list[_RateRow]:
    """Parse a tiered rate table (GHS/GMM/SP format).

    Layout:
      row 0 (tier_row): "Rate :" ... EO ... ES ... EC ... EF ... Premium
      row 1: Insured | Plan | Rate | Premium | Rate | Premium ...
      row 2+: data

    Tier tokens canonicalize via the registry schemes; dependant-only tokens
    (SO/CO/FO/SC, Spouse/Child) keep dependant-scope keys. Non-numeric rate
    cells ("refer to local tab" cross-references) yield no tier entry.
    """
    tier_header = rows[tier_row] if tier_row < len(rows) else []
    tier_cols = _tier_cells(tier_header)

    # The sub-header row with "Rate" / "Premium" labels is usually tier_row+1.
    sub_header_idx = tier_row + 1
    if sub_header_idx >= len(rows):
        return []

    # Detect Plan and Insured columns. GHS/GMM put these on the sub-header row;
    # OSI (secondment) puts "Insured" + "Plan / Region" on the tier-header row
    # instead, with only Rate/Premium labels on the sub-header. Scan
    # the sub-header FIRST (the row aligned with the data) and fall back to the
    # tier header, so a descriptive label on the anchor row can't steal the key
    # column from the real per-row plan code (without a key column every data row
    # is skipped).
    plan_col = -1
    insured_col = -1
    for scan_row in (rows[sub_header_idx], tier_header):
        for c, val in enumerate(scan_row or []):
            if val is None:
                continue
            s = str(val).strip().lower()
            if plan_col < 0 and (s.startswith("plan") or s.startswith("category")):
                plan_col = c
            if insured_col < 0 and s.startswith("insured"):
                insured_col = c

    # Walk data rows starting after the sub-header.
    out: list[_RateRow] = []
    current_insured = ""
    for i in range(sub_header_idx + 1, min(len(rows), tier_row + 30)):
        row = rows[i] or []
        text = _row_text(row)
        if not text.strip():
            continue
        if _rate_section_ended(row):
            break
        # A repeated column-header row introduces another insured entity's
        # block — skip it rather than parsing it as data.
        if _is_rate_header_row(row):
            continue

        ins = _read_insured(row, insured_col)
        if ins:
            current_insured = ins

        plan_val = ""
        if 0 <= plan_col < len(row) and _non_empty(row[plan_col]):
            # Normalize plan number (e.g., "1.0" → "1")
            plan_val = _int_code(row[plan_col])

        if not plan_val:
            continue

        tiers: dict[str, dict[str, float]] = {}
        total_premium = 0.0
        for tier_name, tier_col in tier_cols.items():
            rate_col = tier_col
            prem_col = tier_col + 1
            r = _safe_float(row, rate_col)
            p = _safe_float(row, prem_col)
            if r is not None or p is not None:
                tiers[tier_name] = {"rate": r or 0.0, "premium": p or 0.0}
                total_premium += p or 0.0

        if not tiers:
            continue

        out.append(_RateRow(
            key=plan_val,
            rate_basis="tiered",
            annual_premium=total_premium if total_premium > 0 else None,
            rate_tiers=tiers,
            insured=current_insured,
            # Tiered keys ARE plan codes, so composite keys ("1A/1B" covering
            # two basis-of-cover plans, CBRE GHS) safely expand into their
            # tokens at enrich time — unlike category-text keys.
            expand_tokens=True,
        ))

    return out


def _parse_flat_rates(
    rows: list[list[Cell]], rate_start: int, is_per_1000: bool
) -> list[_RateRow]:
    """Parse flat-rate table (GTL/GPA/GBT/WICI format).

    Layouts vary but share: Category | [Sum Insured] | Rate | Annual Premium
    """
    # The rate_start row is the header — detect column positions.
    header = rows[rate_start] if rate_start < len(rows) else []
    cat_col = rate_col = si_col = prem_col = insured_col = -1
    earnings_col = plan_col = -1
    data_start = rate_start + 1

    def _is_rate_label(h: str) -> bool:
        """True only for the section label 'Rate :' (colon required), not a
        bare 'Rate' data-column header. The colon is what distinguishes the
        section anchor (which `_find_rate_section_start` also keys on) from a
        column whose header is literally 'Rate' (e.g. WICA's earnings table) —
        skipping the latter would drop the rate column entirely."""
        return bool(re.match(r"rate\s*:\s*$", h))

    def _scan_header(scan_row: list[Cell]) -> None:
        nonlocal cat_col, si_col, rate_col, prem_col, insured_col, earnings_col
        nonlocal plan_col
        for c, val in enumerate(scan_row):
            if val is None:
                continue
            h = str(val).strip().lower()
            if _is_rate_label(h):
                continue
            if insured_col < 0 and h.startswith("insured"):
                insured_col = c
            elif cat_col < 0 and "category" in h:
                cat_col = c
            # Per-member rate tables (GP/SP/GCGP/GCSP/GD) key the rate row on a
            # "Plan" column instead of "Category"; captured as a fallback key.
            elif plan_col < 0 and h.startswith("plan"):
                plan_col = c
            elif si_col < 0 and "sum" in h and ("insured" in h or "assured" in h):
                si_col = c
            # Rate is matched before earnings: a combined header such as
            # "Rate on Earnings" is a rate column, and the earnings amount
            # column never contains the word "rate" in any known template.
            # "Per Insured" / "Member Rate" are the per-member rate labels, and
            # "Premium Rate (Per Trip)" (VDL GBT) is a RATE column despite
            # containing the word premium.
            elif rate_col < 0 and (
                ("rate" in h and "premium" not in h)
                or "premium rate" in h
                or "per insured" in h
                or "member rate" in h
            ):
                rate_col = c
            elif earnings_col < 0 and "earning" in h:
                earnings_col = c
            elif prem_col < 0 and "premium" in h and "premium rate" not in h:
                prem_col = c

    _scan_header(header)

    # Sometimes "Rate :" sits alone on its row with the column labels one or two
    # rows below (a blank spacer row is common, e.g. CBRE/CDL Dental). The
    # per-$1,000 marker moves down with those labels (Hartree GTL), so
    # re-detect it on the row that actually carried them.
    if cat_col < 0 and plan_col < 0:
        for off in (1, 2):
            idx = rate_start + off
            if idx >= len(rows):
                break
            _scan_header(rows[idx] or [])
            if cat_col >= 0 or plan_col >= 0:
                labels_text = " ".join(str(c) for c in (rows[idx] or []) if c).lower()
                if "1,000" in labels_text or "1000" in labels_text:
                    is_per_1000 = True
                data_start = idx + 1
                break
    elif rate_col < 0 and rate_start + 1 < len(rows):
        # Header found but the rate label ("Member Rate" / "Per Insured") is on
        # the next row. Scan ONLY for the rate column so a data row can't reassign
        # the other column indices (review finding #4).
        for c, val in enumerate(rows[rate_start + 1] or []):
            if val is None:
                continue
            h = str(val).strip().lower()
            if (("rate" in h and "premium" not in h)
                    or "per insured" in h or "member rate" in h):
                rate_col = c
                data_start = rate_start + 2
                break

    # Per-member tables have no Category column — fall back to the Plan column
    # as the row key (compound codes like "1A/1B" are expanded at enrich time).
    key_col = cat_col if cat_col >= 0 else plan_col

    # Merged-cell shift correction (mirrors the Basis-of-Cover walk): when the
    # key column holds at most one value across the data rows but a nearby
    # unclaimed column holds several, the header was printed over the insured
    # column and the real keys live to its right (VDL WICA).
    if key_col >= 0:
        claimed = {si_col, rate_col, prem_col, earnings_col, insured_col, plan_col}
        window = rows[data_start : data_start + 10]

        def _count(col: int) -> int:
            return sum(
                1 for r in window if r and col < len(r) and _non_empty(r[col])
            )

        if _count(key_col) <= 1:
            for col in range(key_col + 1, key_col + 4):
                if col in claimed:
                    continue
                if _count(col) > 1:
                    if insured_col < 0:
                        insured_col = key_col
                    key_col = col
                    break

    # A Rate section that lists ONLY an Annual Premium column (no per-member rate,
    # no Sum Insured, no earnings) is a flat-annual product (GBT travel): one
    # policy premium covering everyone, printed once against the first category.
    # The premium cell is often annotated ("$3,169.80 (Subject to Minimum …)"),
    # so it parses via _currency_amount rather than _safe_float.
    is_annual_flat = (
        not is_per_1000 and rate_col < 0 and si_col < 0 and earnings_col < 0
        and prem_col >= 0
    )

    out: list[_RateRow] = []
    current_insured = ""
    for i in range(data_start, min(len(rows), rate_start + 30)):
        row = rows[i] or []
        text = _row_text(row)
        if not text.strip():
            continue
        if _rate_section_ended(row):
            break
        # A repeated Insured/Category header row marks the next entity's block.
        if _is_rate_header_row(row):
            ins = _read_insured(row, insured_col)
            if ins:
                current_insured = ins
            continue

        ins = _read_insured(row, insured_col)
        if ins:
            current_insured = ins

        cat_val = ""
        if 0 <= key_col < len(row) and _non_empty(row[key_col]):
            cat_val = _norm(row[key_col])
        if not cat_val:
            continue
        cat_lower = cat_val.lower()
        if cat_lower.startswith("insured") or cat_lower in ("category", "plan"):
            continue

        si = _safe_float(row, si_col)
        prem_note: str | None = None
        if is_annual_flat:
            prem = _currency_amount(row, prem_col)
            # Keep the full annotated text ("Subject to Minimum Policy
            # Premium…") when the amount had to be parsed out of prose.
            if (
                prem is not None
                and _safe_float(row, prem_col) is None
                and 0 <= prem_col < len(row)
                and row[prem_col] is not None
            ):
                prem_note = _norm(row[prem_col])
        else:
            prem = _safe_float(row, prem_col)
        rate = _safe_float(row, rate_col)
        # Earnings cells can carry a currency symbol / annotation just like the
        # premium ("S$71,960,473 (estimated)"), so parse them the same way.
        earn = _currency_amount(row, earnings_col)

        if rate is None and prem is None:
            continue

        if is_per_1000:
            basis = "per_1000_si"
        elif earnings_col >= 0:
            basis = "earnings_based"
        elif is_annual_flat:
            basis = "annual_flat"
        else:
            basis = "flat"

        out.append(_RateRow(
            key=cat_val,
            rate_basis=basis,
            rate=rate,
            sum_insured=si,
            annual_premium=prem,
            insured=current_insured,
            # Only per-member tables (keyed on the Plan column, no Category) get
            # compound-token expansion at enrich time.
            expand_tokens=(cat_col < 0),
            member_type=_member_type(cat_val) if cat_col < 0 else None,
            estimated_annual_earnings=earn,
            premium_note=prem_note,
        ))

    return out


def _blended_product_rate(
    categories: tuple[ExtractedCategory, ...],
    rate_data: list[_RateRow],
) -> float | None:
    """The single product-wide per-S$1000 rate when a sheet states ONE blended
    rate on the total sum insured instead of a rate per category (the GPA
    pattern: a lone "All Employees" row carrying the rate on the summed SI).

    Returned only when the whole rate table is one ``per_1000_si`` row whose
    ``sum_insured`` exceeds the largest single category's — i.e. it's a total
    across everyone, not one category's figure. GTL/GCI (a rate row per plan)
    and genuine single-category products (row SI == the category's SI) keep
    normal per-row matching and return None.
    """
    if len(rate_data) != 1:
        return None
    row = rate_data[0]
    if row.rate_basis != "per_1000_si" or not isinstance(row.rate, (int, float)):
        return None
    if not isinstance(row.sum_insured, (int, float)):
        return None
    cat_si = [c.sum_insured for c in categories if isinstance(c.sum_insured, (int, float))]
    # A blend only makes sense across MULTIPLE priced categories, and the row's SI
    # must strictly exceed the largest single category's (small tolerance for float
    # noise) — i.e. it's a total across everyone, not one category's own SI. The
    # ``>= 2`` guard prevents a lone single-category rate (or a product whose
    # categories carry no parsed SI, max()==0) from being mistaken for a blend.
    if len(cat_si) >= 2 and row.sum_insured > max(cat_si) * 1.0001:
        return row.rate
    return None


def _enrich_with_rates(
    categories: tuple[ExtractedCategory, ...],
    rate_data: list[_RateRow],
) -> tuple[ExtractedCategory, ...]:
    """Match rate data to categories and create enriched instances.

    A single sheet can list the same category text under multiple insured
    entities (e.g. WICA's two STM companies), each with its own premium. We
    therefore index *all* rate rows per key (not last-wins) and, when several
    candidates share a key, pick the one whose insured entity matches the
    category. This keeps single-entity sheets working (one candidate) while
    fixing the cross-entity premium bleed.
    """
    if not rate_data:
        return categories

    # A single blended per-S$1000 rate (GPA) states no per-category rate. Apply it
    # to EVERY category so each member's premium computes from their own basis
    # (basis / 1000 x rate) downstream. Keep each category's own sum_insured and
    # drop the group annual_premium — it's the whole product's total, not one
    # category's, so it must not be mistaken for a single line's premium.
    blended = _blended_product_rate(categories, rate_data)
    if blended is not None:
        return tuple(
            replace(
                cat,
                premium_rate=blended,
                rate_basis="per_1000_si",
                annual_premium=None,
            )
            for cat in categories
        )

    def _norm_ins(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip().lower()

    def _plan_tokens(key: str) -> set[str]:
        """Lower-cased candidate plan codes for matching a rate key to a category.

        Shares `split_plan_codes` with the reconciler so both stages agree on how
        a composite/annotated key ("1A/1B", "B1 & B", "1 - Employees") splits.
        A tier-token suffix ("2 - SO/CO") names WHO the rate prices, not more
        plan codes — only the lead code is a plan there, so SO/CO can't be
        mistaken for (or hijack) plan codes.
        """
        if _tier_suffix_keys(key):
            lead = re.split(r"\s*-\s*", key, maxsplit=1)[0].strip()
            return {t.lower() for t in split_plan_codes(lead)}
        return {c.lower() for c in split_plan_codes(key)}

    # Build lookup indices: by plan code and by category text. Each maps to a
    # list of candidate rows (insured-disambiguated at match time).
    rate_by_plan: dict[str, list[_RateRow]] = {}
    rate_by_text: dict[str, list[_RateRow]] = {}
    for rd in rate_data:
        normalized_key = rd.key.strip().lower()
        rate_by_plan.setdefault(normalized_key, []).append(rd)
        # Also index by each bundled plan token so compound per-member keys match
        # a category's bare plan_code. Gated to per-member rate rows (rd.expand_
        # tokens) so a digit-leading category-text key in a flat/per_1000/earnings
        # table can't hijack a category by token (review finding #3). Exact keys
        # above are still tried first.
        if rd.expand_tokens:
            for tok in _plan_tokens(rd.key):
                if tok != normalized_key:
                    rate_by_plan.setdefault(tok, []).append(rd)
        m = _PLAN_INLINE.match(rd.key)
        text_key = m.group(2).strip().lower() if m else normalized_key
        rate_by_text.setdefault(text_key, []).append(rd)

    def _pick(candidates: list[_RateRow], insured: str) -> _RateRow | None:
        if not candidates:
            return None
        target = _norm_ins(insured)
        if target:
            for rd in candidates:
                if _norm_ins(rd.insured) == target:
                    return rd
        return candidates[0]

    enriched: list[ExtractedCategory] = []
    for cat in categories:
        # Try matching by plan_code first (for tiered formats: "1", "2", etc.)
        rd = None
        if cat.plan_code:
            # Normalize numeric plan codes: "1.0" → "1"
            plan_key = _int_code(cat.plan_code).lower()
            rd = _pick(rate_by_plan.get(plan_key, []), cat.insured)
        # Then by category text
        if rd is None:
            rd = _pick(rate_by_text.get(cat.category.strip().lower(), []), cat.insured)
        # Try with "Plan X: <category>" pattern
        if rd is None and cat.plan_code:
            key = f"plan {cat.plan_code}: {cat.category}".lower()
            rd = _pick(rate_by_text.get(key, []), cat.insured)

        if rd is None:
            enriched.append(cat)
            continue

        # A per-member plan carries a per-dependant rate when it lists either a
        # separate "Dependents" row (e.g. GCGP "1 - Employees" / "1 - Dependents
        # $396.90") or a combined "Employees / Dependents" row (one rate covers both,
        # e.g. "2 - Employees / Dependents $454" → dependant rate = $454). Capture it
        # so dependant coverage prices per dependant from the slip.
        dep_rate = None
        if cat.plan_code and rd.member_type == "both":
            dep_rate = rd.rate
        elif cat.plan_code and rd.member_type in ("employee", None):
            plan_key = _int_code(cat.plan_code).lower()
            ins = _norm_ins(rd.insured)
            for c in rate_by_plan.get(plan_key, []):
                if c.member_type == "dependent" and (
                    not ins or _norm_ins(c.insured) == ins
                ):
                    dep_rate = c.rate
                    break

        # Tier-token suffixed siblings ("2 - EO" / "2 - SO/CO") split ONE plan's
        # per-member rate by member tier. Surface the split as rate_tiers so the
        # category setup shows a cell per tier instead of silently keeping only
        # the first row's figure. Genuinely tiered tables keep their own tiers.
        rate_tiers = rd.rate_tiers
        if rate_tiers is None and cat.plan_code and rd.expand_tokens:
            plan_key = _int_code(cat.plan_code).lower()
            ins = _norm_ins(rd.insured)
            tier_cells: dict[str, dict[str, float]] = {}
            for c in rate_by_plan.get(plan_key, []):
                if ins and _norm_ins(c.insured) not in ("", ins):
                    continue
                for tier in _tier_suffix_keys(c.key):
                    tier_cells.setdefault(
                        tier,
                        {"rate": c.rate or 0.0, "premium": c.annual_premium or 0.0},
                    )
            if tier_cells:
                rate_tiers = tier_cells

        enriched.append(replace(
            cat,
            premium_rate=rd.rate,
            annual_premium=rd.annual_premium,
            rate_basis=rd.rate_basis,
            rate_tiers=rate_tiers,
            sum_insured=cat.sum_insured or rd.sum_insured,
            dependant_rate=dep_rate,
            estimated_annual_earnings=rd.estimated_annual_earnings,
            premium_note=rd.premium_note,
        ))

    return _propagate_annual_flat(tuple(enriched), rate_data)


def _propagate_annual_flat(
    categories: tuple[ExtractedCategory, ...],
    rate_data: list[_RateRow],
) -> tuple[ExtractedCategory, ...]:
    """Spread a single policy-level premium onto the sheet's unpriced categories.

    A flat-annual product (GBT travel) states ONE premium covering everyone,
    printed against the first category only — the sibling categories would
    otherwise show blank financials. Applies only when the whole rate table is
    that one ``annual_flat`` row; sheets with any per-row pricing keep normal
    matching. The premium stays policy-level downstream (``member_financials``
    drops it from per-member views).
    """
    if len(rate_data) != 1 or rate_data[0].rate_basis != "annual_flat":
        return categories
    src = rate_data[0]
    if src.annual_premium is None:
        return categories
    return tuple(
        replace(
            c,
            annual_premium=src.annual_premium,
            rate_basis="annual_flat",
            premium_note=src.premium_note,
        )
        if c.rate_basis is None
        and c.premium_rate is None
        and c.annual_premium is None
        and c.rate_tiers is None
        else c
        for c in categories
    )
