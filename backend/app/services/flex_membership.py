"""Flex membership: resolve each employee's family status + flex tier from the
uploaded employee & dependant listings, and aggregate headcounts.

This is the read-only counterpart to ``member_counts`` for Flexible Benefits.
Unlike insured products (which match employees to a ``Category`` via the matching
engine), a Flex wallet is sized by the employee's **family status** — and the
authoritative source for that is the *dependant listing*: a linked spouse record
means "married", and the number of linked child records sets the +children tier.

Resolution order per employee (most authoritative first):

1. **Dependant records** — if the employee has any linked, active dependants,
   derive S/M/M1C/M2C/M3C from them (spouse present + child count). This is the
   "based on the employee & dependant listing" path.
2. **Derived roster attribute** — the ``family_status`` derived from the free-text
   ``category`` column (see ``seed_demo`` derivation rule).
3. **Marital-status roster column** — a last-resort married/single signal.

Each employee is also best-effort assigned to a scheme tier (by nationality →
country, then job-grade band) so the broker can see how many people fall into each
limit. It never writes anything back — derived attributes are computed into
throwaway dicts, exactly like ``member_counts``.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Dependant, Employee, EmployeeAttributeSchema, FlexScheme, PolicyYear
from app.models.dependant import DEPENDANT_STATUS_TERMINATED
from app.models.employee import EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED
from app.services import flex_proration
from app.services.derivation_engine import derive
from app.services.flex_pricing_resolver import (
    _dependant_eligible,
    scheme_dependant_age_limits,
)
from app.services.flex_proration import ProrationConfig
from app.services.roster_attributes import resolved_last_day

# Canonical family-status codes (mirror the seeded ``family_status`` enum).
FAMILY_CODES: tuple[str, ...] = ("S", "M", "M1C", "M2C", "M3C")

# Platform default currency, used when neither a tier nor the scheme sets one
# (clients are Singapore-based). Override per deploy via INSPRO_DEFAULT_CURRENCY.
# The frontend `lib/flex.ts` mirrors the SGD default only to seed the empty
# currency dropdown; the *resolved* currency always flows from API responses.
DEFAULT_CURRENCY = (os.environ.get("INSPRO_DEFAULT_CURRENCY", "").strip().upper() or "SGD")

# Relationship classification for dependant records. Matched case-insensitively
# against the dependant's ``relationship`` attribute.
_SPOUSE_WORDS = ("spouse", "husband", "wife", "partner")
# NB: no bare "step" — stepchildren already match via child/children/son/daughter,
# whereas "step" alone would miscount a "stepmother"/"stepfather" as a child.
_CHILD_WORDS = ("child", "children", "son", "daughter", "kid")

# Nationality token → country (lowercased). Covers the STM Flex programs
# (Singapore / Thailand / Vietnam / Indonesia) plus common neighbours.
_NATIONALITY_COUNTRY: dict[str, str] = {
    "singapore": "singapore", "singaporean": "singapore", "sg": "singapore",
    "sgp": "singapore",
    "malaysia": "malaysia", "malaysian": "malaysia", "my": "malaysia",
    "thailand": "thailand", "thai": "thailand", "th": "thailand",
    "vietnam": "vietnam", "vietnamese": "vietnam", "viet nam": "vietnam",
    "vn": "vietnam",
    "indonesia": "indonesia", "indonesian": "indonesia", "id": "indonesia",
    "india": "india", "indian": "india",
    "philippines": "philippines", "filipino": "philippines",
    "filipina": "philippines", "ph": "philippines",
    "china": "china", "chinese": "china",
}


def _meta_date(meta: dict[str, Any], key: str) -> date | None:
    """An ISO date from the scheme meta, or None (tolerates legacy junk — the
    save/confirm validation guards new writes)."""
    raw = meta.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def flex_effective_window(
    db: Session, policy_year: PolicyYear
) -> tuple[date, date]:
    """The flex scheme's effective coverage window.

    ``meta.effective_start`` / ``meta.effective_end`` when set; each bound falls
    back to the policy year's span (the sparse-override pattern of
    ``ProductTerm``), so schemes without explicit dates behave exactly as before.
    """
    row = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year.id)
    ).scalar_one_or_none()
    scheme = (row.scheme or {}) if row is not None else {}
    meta = scheme.get("meta") if isinstance(scheme.get("meta"), dict) else {}
    start = _meta_date(meta, "effective_start") or policy_year.start_date
    end = _meta_date(meta, "effective_end") or policy_year.end_date
    return start, end


def _coerce_int(value: object) -> int | None:
    """Best-effort int from a derived/raw attribute (handles '17', 17.0, etc.)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        return int(m.group()) if m else None
    return None


def _grade_source(derived: dict[str, Any], raw_attrs: dict) -> object:
    """The employee's grade value, preferring the derived attribute. Uses an
    explicit None/"" check (not truthiness) so an integer grade of 0 survives."""
    for src in (derived, raw_attrs):
        v = src.get("grade")
        if v is not None and v != "":
            return v
    return None


def _grade_token(raw: object) -> str | None:
    """Canonical raw-grade string for exact match-set membership. An integral
    float collapses to its int form ("18.0" → "18") so it can't diverge from an
    integer 18 elsewhere on the roster."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    s = str(raw).strip()
    return s or None


def employee_signals(
    derived: dict[str, Any], raw_attrs: dict
) -> tuple[int | None, str | None, str | None]:
    """The three matching inputs for one employee, resolved from ONE place so the
    vocabulary, the live match, and assignment can never drift: the numeric grade
    (legacy band), the canonical raw-grade token (explicit sets), and the job
    designation."""
    grade_raw = _grade_source(derived, raw_attrs)
    return (
        _coerce_int(grade_raw),
        _grade_token(grade_raw),
        employee_designation(derived, raw_attrs),
    )


def classify_relationship(raw: object) -> str | None:
    """Classify a dependant's relationship as ``"spouse"``, ``"child"``, or None."""
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if any(w in s for w in _SPOUSE_WORDS):
        return "spouse"
    if any(w in s for w in _CHILD_WORDS):
        return "child"
    return None


def count_dependants(deps: list[Dependant]) -> tuple[int, int]:
    """Return (spouse_count, child_count) across a list of dependant records."""
    spouse = 0
    child = 0
    for d in deps:
        av = d.attribute_values or {}
        kind = classify_relationship(av.get("relationship") or av.get("relation"))
        if kind == "spouse":
            spouse += 1
        elif kind == "child":
            child += 1
    return spouse, child


def family_status_from_counts(married: bool, children: int) -> str:
    """Map (married, #children) onto a canonical S/M/M1C/M2C/M3C code.

    Any children present put the employee in the +children family band (the enum
    has no "single parent" code), capped at 3+.
    """
    if not married and children <= 0:
        return "S"
    if children <= 0:
        return "M"
    if children == 1:
        return "M1C"
    if children == 2:
        return "M2C"
    return "M3C"


def resolve_family_status(
    derived: dict[str, Any], raw_attrs: dict, spouse_count: int, child_count: int, has_deps: bool
) -> tuple[str | None, str]:
    """Resolve an employee's family status + the source it came from.

    Returns ``(code, source)`` where source is ``"dependants"`` | ``"roster"`` |
    ``"none"``.
    """
    # Dependant records are authoritative only when they actually identify a
    # spouse or child. A linked dependant with an unrecognized relationship
    # (e.g. "parent") must NOT silently downgrade a married employee to Single —
    # fall through to the roster signals instead.
    if has_deps and (spouse_count > 0 or child_count > 0):
        return family_status_from_counts(spouse_count > 0, child_count), "dependants"

    derived_fs = derived.get("family_status")
    if derived_fs in FAMILY_CODES:
        return derived_fs, "roster"

    marital = str(raw_attrs.get("marital_status") or "").strip().lower()
    if marital:
        if any(w in marital for w in ("married", "spouse")):
            return "M", "roster"
        if any(
            w in marital
            for w in ("single", "unmarried", "divorced", "widow", "separat")
        ):
            return "S", "roster"
    return None, "none"


def nationality_country(nationality: object) -> str | None:
    """Normalize a nationality string to a country token, or None."""
    s = str(nationality or "").strip().lower()
    if not s:
        return None
    if s in _NATIONALITY_COUNTRY:
        return _NATIONALITY_COUNTRY[s]
    for token, country in _NATIONALITY_COUNTRY.items():
        if token in s:
            return country
    return None


def _tier_band(tier: dict[str, Any]) -> tuple[int | None, int | None]:
    et = tier.get("employee_type") if isinstance(tier.get("employee_type"), dict) else {}
    return _coerce_int(et.get("job_grade_min")), _coerce_int(et.get("job_grade_max"))


def _tier_has_band(tier: dict[str, Any]) -> bool:
    lo, hi = _tier_band(tier)
    return lo is not None or hi is not None


def _grade_in_band(grade: int | None, tier: dict[str, Any]) -> bool:
    if grade is None:
        return False
    lo, hi = _tier_band(tier)
    if lo is None and hi is None:
        return False
    if lo is not None and grade < lo:
        return False
    if hi is not None and grade > hi:
        return False
    return True


def _band_width(tier: dict[str, Any]) -> int:
    lo, hi = _tier_band(tier)
    if lo is not None and hi is not None:
        return hi - lo
    return 9_999  # half-open / unbounded bands are the least specific


def _country_matches(tier_country: object, emp_country: str | None) -> bool:
    raw = str(tier_country or "").strip()
    if not raw or emp_country is None:
        return False
    tc = nationality_country(raw) or raw.lower()
    return tc == emp_country


# ── Designation / job-title eligibility ──────────────────────────────────────
# Many schemes tier eligibility by *job title* (GCEO, Manager, Officer, …) rather
# than a numeric job-grade band — the tier's name / eligibility text IS the
# criterion, matched against the employee's designation. Roster attribute keys
# that may carry that designation, most specific first; ``category`` is the
# free-text column the roster commonly stores it in.
_DESIGNATION_KEYS: tuple[str, ...] = (
    "designation", "job_title", "title", "position",
    "grade_name", "job_grade_name", "category",
)
# Phrases that mark a band-less tier as a generic catch-all (matches everyone in
# its pool) rather than a specific job-title group.
_CATCH_ALL_WORDS: tuple[str, ...] = (
    "all employee", "all other", "everyone", "default", "remaining",
)


def _normalize_label(value: object) -> str:
    """Lowercase, expand ``&``→``and``, collapse punctuation/space for matching."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tier_labels(tier: dict[str, Any]) -> set[str]:
    """Normalized job-title labels a tier can be matched by (name + raw text)."""
    et = tier.get("employee_type") if isinstance(tier.get("employee_type"), dict) else {}
    labels = {_normalize_label(tier.get("name")), _normalize_label(et.get("raw"))}
    labels.discard("")
    return labels


def _is_catch_all(tier: dict[str, Any]) -> bool:
    """A band-less tier with no specific job-title label — a pool's fallback."""
    et = tier.get("employee_type") if isinstance(tier.get("employee_type"), dict) else {}
    raw = str(et.get("raw") or "").strip().lower()
    name = str(tier.get("name") or "").strip().lower()
    if not raw and not name:
        return True
    return any(w in f"{raw} {name}" for w in _CATCH_ALL_WORDS)


def employee_designation(derived: dict[str, Any], raw_attrs: dict) -> str | None:
    """The employee's job-title / designation string, or None."""
    for key in _DESIGNATION_KEYS:
        for src in (derived, raw_attrs):
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _match_designation(
    designation: str | None, pool: list[int], tiers: list[dict[str, Any]]
) -> int | None:
    """Index of the pool tier whose label equals the employee's designation."""
    target = _normalize_label(designation)
    if not target:
        return None
    for i in pool:
        if target in _tier_labels(tiers[i]):
            return i
    return None


# ── Roster-anchored explicit match sets (reconciliation model) ────────────────
# The broker reconciles each tier against the roster by selecting the actual
# grade / designation values employees carry. Because the values come straight
# from the roster, matching is exact (normalized) set-membership — no fuzzy band
# math — and an employee matches a tier if EITHER axis hits (union). Empty sets
# mean the tier hasn't been reconciled, so matching falls back to the legacy
# numeric band + job-title label heuristics below.


def _tier_match_sets(tier: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Normalized (grades, designations) value sets a tier explicitly claims."""
    et = tier.get("employee_type") if isinstance(tier.get("employee_type"), dict) else {}
    raw_grades = et.get("match_grades")
    raw_desigs = et.get("match_designations")
    grades = {
        _normalize_label(g)
        for g in (raw_grades if isinstance(raw_grades, list) else [])
        if str(g).strip()
    }
    desigs = {
        _normalize_label(d)
        for d in (raw_desigs if isinstance(raw_desigs, list) else [])
        if str(d).strip()
    }
    grades.discard("")
    desigs.discard("")
    return grades, desigs


def _tier_has_match_sets(tier: dict[str, Any]) -> bool:
    grades, desigs = _tier_match_sets(tier)
    return bool(grades or desigs)


def _tier_explicit_match(
    tier: dict[str, Any], grade_str: str | None, designation: str | None
) -> bool:
    """Union rule: the employee's grade OR designation is in the tier's sets."""
    grades, desigs = _tier_match_sets(tier)
    if grades and _normalize_label(grade_str) in grades:
        return True
    if desigs and _normalize_label(designation) in desigs:
        return True
    return False


def explicit_match_indices(
    grade_str: str | None,
    designation: str | None,
    country: str | None,
    tiers: list[dict[str, Any]],
) -> list[int]:
    """Every tier whose roster-anchored match sets the employee satisfies (union),
    within the same country pool ``match_tier`` uses. A length > 1 means the
    employee is *ambiguous* (the reconciliation preview surfaces these)."""
    if not tiers:
        return []
    idxs = list(range(len(tiers)))
    country_idxs = [i for i in idxs if _country_matches(tiers[i].get("country"), country)]
    default_idxs = [i for i in idxs if not str(tiers[i].get("country") or "").strip()]
    # Mirror the universe match_tier can assign from: the country pool AND the
    # no-country default pool it falls back to (disjoint, so concatenation is
    # dedup-free) — otherwise a cross-pool overlap is missed and the assigned tier
    # can be absent from the reported set.
    if country_idxs and default_idxs:
        pool = country_idxs + default_idxs
    else:
        pool = country_idxs or default_idxs or idxs
    return [i for i in pool if _tier_explicit_match(tiers[i], grade_str, designation)]


def match_tier(
    grade: int | None,
    country: str | None,
    tiers: list[dict[str, Any]],
    designation: str | None = None,
    grade_str: str | None = None,
) -> int | None:
    """Index of the tier an employee belongs to, or None when not eligible.

    Country (from nationality) narrows the pool first; tiers with no country act
    as defaults. Within the pool, resolution is: roster-anchored explicit match
    sets (grade OR designation, union) → narrowest grade band containing the
    employee's grade → exact job-title (designation) match against a band-less
    tier's label → a band-less catch-all.

    ``grade_str`` is the employee's raw grade value (e.g. ``"JG08"``) used for the
    explicit match sets; ``grade`` is the numeric coercion used by the legacy
    band. A *known* grade that falls outside every banded tier — or a known
    designation that matches no tier — yields None (the employee genuinely isn't
    eligible, so no wallet is assigned), unless the pool has a generic catch-all.
    Only when neither a grade nor a designation is known does the first pool tier
    apply, so rosters missing both columns still get a best-effort estimate.
    """
    if not tiers:
        return None
    idxs = list(range(len(tiers)))
    country_idxs = [i for i in idxs if _country_matches(tiers[i].get("country"), country)]
    default_idxs = [i for i in idxs if not str(tiers[i].get("country") or "").strip()]
    pool = country_idxs or default_idxs or idxs

    hit = _match_in_pool(grade, grade_str, designation, pool, tiers)
    if hit is not None:
        return hit
    # Country matched but the employee is out of band / off-title there: fall back
    # to a no-country default tier (these "act as defaults", per the country
    # model) before declaring ineligible.
    if country_idxs and default_idxs:
        hit = _match_in_pool(grade, grade_str, designation, default_idxs, tiers)
        if hit is not None:
            return hit
    # Nothing matched: default to the first pool tier ONLY when we have neither a
    # grade nor a designation to disambiguate (best-effort for sparse rosters). A
    # known grade/designation that matched nothing is genuinely ineligible — never
    # collapse a designation-labeled roster onto the first tier.
    if grade is None and not _normalize_label(designation):
        return pool[0] if pool else None
    return None


def _match_in_pool(
    grade: int | None,
    grade_str: str | None,
    designation: str | None,
    pool: list[int],
    tiers: list[dict[str, Any]],
) -> int | None:
    """Within a candidate pool: roster-anchored explicit match sets first (grade
    OR designation), then narrowest grade band containing the grade, then an exact
    job-title match, then a band-less catch-all. None when none apply.

    Explicit sets are authoritative: once a tier has been reconciled against the
    roster, its selections win over the legacy band/label heuristics. Ties among
    explicit matches resolve to document order here; the reconciliation preview
    surfaces them as ambiguous so the broker can disambiguate."""
    explicit = [i for i in pool if _tier_explicit_match(tiers[i], grade_str, designation)]
    if explicit:
        return explicit[0]

    graded = [i for i in pool if _grade_in_band(grade, tiers[i])]
    if graded:
        return min(graded, key=lambda i: _band_width(tiers[i]))
    hit = _match_designation(designation, pool, tiers)
    if hit is not None:
        return hit
    # A band-less tier acts as a default only when it has NO explicit match sets —
    # a reconciled tier is specific and must not absorb employees it didn't select.
    no_band = [
        i
        for i in pool
        if not _tier_has_band(tiers[i]) and not _tier_has_match_sets(tiers[i])
    ]
    if no_band:
        # Prefer an explicit catch-all tier ("all other employees", a band-less
        # default) — it absorbs an off-title employee by design.
        catch = [i for i in no_band if _is_catch_all(tiers[i])]
        if catch:
            return catch[0]
        # No catch-all: fall back to the first band-less tier only when the
        # designation can't disambiguate (none given). A *known* designation that
        # matched no job-title tier is genuinely ineligible — never collapse a
        # designation-labeled roster onto the first tier.
        if not _normalize_label(designation):
            return no_band[0]
    return None


def tier_wallet(tier: dict[str, Any] | None, family_status: str | None, meta: dict) -> float | None:
    """Resolve the wallet amount for a family status: per-family limit, else the
    tier flat cap, else the scheme-level system cap."""
    if tier is None:
        return None
    limits = tier.get("limits") if isinstance(tier.get("limits"), list) else []
    if family_status:
        for row in limits:
            if isinstance(row, dict) and row.get("family_status") == family_status:
                amt = row.get("amount")
                if isinstance(amt, (int, float)):
                    return float(amt)
    cap = tier.get("system_cap")
    if isinstance(cap, (int, float)):
        return float(cap)
    mcap = meta.get("system_cap")
    if isinstance(mcap, (int, float)):
        return float(mcap)
    return None


@dataclass(frozen=True)
class EmployeeFlex:
    employee_id: str
    family_status: str | None
    source: str
    spouse_count: int
    child_count: int
    tier_name: str | None
    currency: str | None
    wallet_amount: float | None
    # Derivation behind a pro-rated wallet; None = the full annual allowance.
    proration: dict[str, Any] | None = None


@dataclass(frozen=True)
class TierHeadcount:
    name: str
    country: str | None
    currency: str | None
    eligible: int
    by_family_status: dict[str, int]
    wallet_by_family_status: dict[str, float | None]


@dataclass
class FlexMembership:
    employees_total: int
    family_status_counts: dict[str, int]
    source_counts: dict[str, int]
    tiers: list[TierHeadcount]
    assignments: list[EmployeeFlex] = field(default_factory=list)
    scheme_status: str | None = None
    # Active employees who matched no eligibility tier (so carry no wallet) —
    # only meaningful once the scheme has tiers. Bucketed by the designation that
    # failed to match, so the broker can fix the roster or add a tier.
    ineligible_count: int = 0
    ineligible_designations: dict[str, int] = field(default_factory=dict)
    # Active employees whose grade/designation satisfies MORE than one tier's
    # reconciled match sets — they're assigned to the first (document order), but
    # the overlap is surfaced so the broker can tighten the tiers.
    ambiguous_count: int = 0
    ambiguous_examples: list[dict[str, Any]] = field(default_factory=list)


# ── Shared per-employee resolution (single source of truth) ───────────────────
# Both the headcount aggregates (``compute_flex_membership``) and the coverage
# validation (``compute_flex_coverage``) resolve each employee's family status +
# flex tier through THIS one function, so the card and the "who's left out" lists
# can never drift apart.


@dataclass(frozen=True)
class ResolvedEmployee:
    employee_id: str
    staff_id: str
    name: str | None
    designation: str | None
    grade: str | None
    nationality: str | None
    marital_raw: str | None
    family_status: str | None
    source: str
    spouse_count: int
    child_count: int
    # Total linked dependants (any relationship) — distinguishes "no dependants at
    # all" from "has dependants, but none is a spouse/child" in coverage messages.
    dependant_count: int
    tier_idx: int | None
    tier_name: str | None
    currency: str | None
    # The EFFECTIVE wallet — pro-rated when the scheme says so. `proration`
    # carries the derivation (None = the full annual allowance).
    wallet_amount: float | None
    # Names of every tier whose reconciled match sets this employee satisfies;
    # length > 1 means ambiguous (assigned to the first, overlap surfaced).
    overlap_tiers: list[str]
    proration: dict[str, Any] | None = None


def resolve_employee(
    emp: Employee,
    emp_deps: list[Dependant],
    derived: dict[str, Any],
    tiers: list[dict[str, Any]],
    meta: dict[str, Any],
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
    proration: ProrationConfig | None = None,
    entitlement: tuple[date, date] | None = None,
) -> ResolvedEmployee:
    """Resolve ONE employee's family status + flex-tier assignment + identity.

    ``age_limits`` + ``ref`` (scheme-wide dependant window + renewal date) drop
    out-of-window dependants from the spouse/child counts, so family status and
    the flex wallet match the coverage/pricing eligibility. Absent → no filtering
    (every classified dependant counts)."""
    raw_attrs = emp.attribute_values or {}
    eligible_deps = (
        [d for d in emp_deps if _dependant_eligible(d, age_limits, ref)]
        if age_limits is not None and ref is not None
        else emp_deps
    )
    spouse_count, child_count = count_dependants(eligible_deps)
    code, source = resolve_family_status(
        derived, raw_attrs, spouse_count, child_count, bool(emp_deps)
    )
    grade, grade_str, designation = employee_signals(derived, raw_attrs)
    country = nationality_country(raw_attrs.get("nationality"))
    idx = match_tier(grade, country, tiers, designation=designation, grade_str=grade_str)
    tier = tiers[idx] if idx is not None else None

    overlap: list[str] = []
    if idx is not None:
        overlaps = explicit_match_indices(grade_str, designation, country, tiers)
        if len(overlaps) > 1:
            overlap = [str(tiers[j].get("name") or f"Tier {j + 1}") for j in overlaps]

    marital_raw = str(raw_attrs.get("marital_status") or "").strip() or None
    nat_raw = str(raw_attrs.get("nationality") or "").strip() or None
    currency = (
        (tier.get("currency") or meta.get("currency") or DEFAULT_CURRENCY)
        if tier
        else None
    )
    # The annual allowance the tier grants, then the member's own share of it.
    # A pro-rated wallet and its derivation are written together or not at all —
    # a bare reduced number with nothing explaining it is unauditable, and this
    # is the figure members dispute.
    full_wallet = tier_wallet(tier, code, meta)
    prorated = flex_proration.prorate(
        full_wallet, emp, entitlement, proration or ProrationConfig()
    )
    return ResolvedEmployee(
        employee_id=emp.id,
        staff_id=emp.staff_id,
        name=emp.employee_name,
        designation=designation,
        grade=grade_str,
        nationality=nat_raw,
        marital_raw=marital_raw,
        family_status=code,
        source=source,
        spouse_count=spouse_count,
        child_count=child_count,
        dependant_count=len(emp_deps),
        tier_idx=idx,
        tier_name=(tier.get("name") if tier else None),
        currency=currency,
        wallet_amount=full_wallet if prorated is None else prorated.amount,
        overlap_tiers=overlap,
        proration=None if prorated is None else prorated.as_dict(),
    )


@dataclass
class ResolvedRoster:
    """Every active employee resolved once, plus the raw dependant listing and the
    active-employee id set — the shared substrate for headcounts, coverage, and
    dependant reconciliation. Loaded through ONE query path so all three views see
    the same roster."""

    resolved: list[ResolvedEmployee]
    dependants: list[Dependant]          # all active deps in the year (linked or not)
    active_emp_ids: set[str]
    emp_by_id: dict[str, Employee]
    tiers: list[dict[str, Any]]
    meta: dict[str, Any]
    scheme_status: str | None
    # Scheme-wide dependant age window + renewal ref, so coverage reconciliation
    # can flag dependants outside it (matching the family-status / wallet filter).
    age_limits: dict[str, dict[str, int]]
    ref: date | None


def _counts_for(dep: Dependant, emp: Employee) -> bool:
    """Whether ``dep`` counts towards ``emp``'s family status — and therefore
    towards the size of their wallet.

    An active dependant always counts. A TERMINATED one counts only while
    resolving a leaver, and only if they were still live on the employee's last
    day. The listing sync terminates a leaver's household in the same apply that
    terminates them, so counting active rows alone would resolve every leaver as
    Single and silently HALVE the settlement figure their sheet is read for. A
    dependant who left earlier (a divorce in March) correctly stops counting.
    """
    if dep.status != DEPENDANT_STATUS_TERMINATED:
        return True
    last_day = resolved_last_day(emp)
    dep_end = resolved_last_day(dep)
    # Excluded ONLY when it is known they left first. An unresolvable date on
    # either side (no `terminated_effective`, or a roster cell reading "end of
    # June") is unknown, not evidence — and defaulting unknown to "drop" is the
    # very failure this function exists to prevent, just reached by the other
    # path: the leaver resolves as Single and the settlement wallet halves.
    if last_day is None or dep_end is None:
        return True
    return dep_end >= last_day


def _entitlement_bounds(
    meta: dict[str, Any], py: PolicyYear | None
) -> tuple[date | None, date | None]:
    """The window a FULL allowance buys: the flex effective window intersected
    with the policy year — the denominator every factor divides by.

    The intersection is what makes a short first year self-healing. CDL's scheme
    carries ``effective_start 2026-07-15`` with an end beyond the year; against a
    hardcoded 12 months every member would pro-rate to ~46% for serving the whole
    scheme period, and against the intersection they resolve to 1.0.
    """
    if py is None:
        return None, None
    start = _meta_date(meta, "effective_start") or py.start_date
    end = _meta_date(meta, "effective_end") or py.end_date
    if start is not None and py.start_date is not None:
        start = max(start, py.start_date)
    if end is not None and py.end_date is not None:
        end = min(end, py.end_date)
    return start, end


def resolve_roster(
    db: Session,
    policy_year_id: str,
    client_id: str | None,
    *,
    with_dependant_detail: bool = True,
    include_terminated: bool = False,
) -> ResolvedRoster:
    """Load + resolve the whole active roster in one place.

    ``with_dependant_detail`` (default True) loads *every* active dependant —
    including orphaned / inactive-linked ones — for the coverage reconciliation.
    The headcount path (``compute_flex_membership``) passes False: it only needs
    spouse/child counts for linked dependants, so the wider query + retained list
    would be wasted work on that hot path.

    ``include_terminated`` (default False) additionally resolves leavers, so
    ``flex_assignment`` can size their settlement wallet instead of nulling it.
    ONLY that caller passes True: ``aggregate_membership`` counts
    ``active_emp_ids`` only, because a leaver in the tier headcounts would
    inflate every eligibility figure on the flex overview.
    """
    scheme_row = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    scheme = (scheme_row.scheme or {}) if scheme_row else {}
    meta = scheme.get("meta") if isinstance(scheme.get("meta"), dict) else {}
    tiers = [t for t in (scheme.get("tiers") or []) if isinstance(t, dict)]

    # Scheme-wide dependant age window + renewal date (ANB is relative to renewal),
    # applied to family-status counts so the wallet isn't sized on ineligible deps.
    age_limits = scheme_dependant_age_limits(meta)
    py = db.get(PolicyYear, policy_year_id)
    ref = py.start_date if py else None

    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    statuses = (
        [EMPLOYEE_STATUS_ACTIVE, EMPLOYEE_STATUS_TERMINATED]
        if include_terminated
        else [EMPLOYEE_STATUS_ACTIVE]
    )
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year_id,
                Employee.status.in_(statuses),
            )
        ).scalars()
    )
    active_emp_ids = {e.id for e in employees if e.status == EMPLOYEE_STATUS_ACTIVE}
    emp_by_id = {e.id: e for e in employees}

    dep_statuses = ["active", DEPENDANT_STATUS_TERMINATED] if include_terminated else ["active"]
    dep_query = select(Dependant).where(
        Dependant.policy_year_id == policy_year_id,
        Dependant.status.in_(dep_statuses),
    )
    if not with_dependant_detail:
        # Headcount only counts linked deps — skip the orphaned/inactive rows.
        dep_query = dep_query.where(Dependant.employee_id.is_not(None))
    all_deps = list(db.execute(dep_query).scalars())

    resolvable = {e.id for e in employees}
    deps_by_emp: dict[str, list[Dependant]] = defaultdict(list)
    for dep in all_deps:
        if dep.employee_id is not None and dep.employee_id in resolvable:
            if _counts_for(dep, emp_by_id[dep.employee_id]):
                deps_by_emp[dep.employee_id].append(dep)

    proration = flex_proration.proration_config(scheme)
    entitlement = flex_proration.entitlement_period(
        *_entitlement_bounds(meta, py)
    )

    resolved = [
        resolve_employee(
            emp, deps_by_emp.get(emp.id, []), derive(emp.attribute_values or {}, schemas),
            tiers, meta, age_limits, ref, proration, entitlement,
        )
        for emp in employees
    ]
    return ResolvedRoster(
        resolved=resolved,
        # The full dependant listing is only needed for coverage reconciliation.
        dependants=all_deps if with_dependant_detail else [],
        active_emp_ids=active_emp_ids,
        emp_by_id=emp_by_id,
        tiers=tiers,
        meta=meta,
        scheme_status=(scheme_row.status if scheme_row else None),
        age_limits=age_limits,
        ref=ref,
    )


def compute_flex_membership(
    db: Session,
    policy_year_id: str,
    client_id: str | None,
    *,
    include_terminated: bool = False,
) -> FlexMembership:
    """Resolve every active employee's family status + flex tier and aggregate.

    ``include_terminated`` additionally resolves leavers into ``assignments``
    (never into the headcounts) so ``flex_assignment`` can size their settlement
    wallet. See ``resolve_roster``.
    """
    roster = resolve_roster(
        db, policy_year_id, client_id,
        with_dependant_detail=False,
        include_terminated=include_terminated,
    )
    return aggregate_membership(roster)


def aggregate_membership(roster: ResolvedRoster) -> FlexMembership:
    """Aggregate a resolved roster into the family-status + tier headcounts."""
    tiers, meta = roster.tiers, roster.meta

    fs_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    tier_emp_counts: dict[int, int] = defaultdict(int)
    tier_fs_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ineligible_designations: dict[str, int] = defaultdict(int)
    assignments: list[EmployeeFlex] = []
    ambiguous_count = 0
    ambiguous_examples: list[dict[str, Any]] = []

    for r in roster.resolved:
        # Assignments cover everyone resolved (leavers included, so their
        # settlement wallet can be written); every COUNT below is active-only,
        # or a leaver would inflate the eligibility figures on the flex overview.
        assignments.append(
            EmployeeFlex(
                employee_id=r.employee_id,
                family_status=r.family_status,
                source=r.source,
                spouse_count=r.spouse_count,
                child_count=r.child_count,
                tier_name=r.tier_name,
                currency=r.currency,
                wallet_amount=r.wallet_amount,
                proration=r.proration,
            )
        )
        if r.employee_id not in roster.active_emp_ids:
            continue

        fs_counts[r.family_status or "unknown"] += 1
        source_counts[r.source] += 1

        if r.tier_idx is not None:
            tier_emp_counts[r.tier_idx] += 1
            if r.family_status:
                tier_fs_counts[r.tier_idx][r.family_status] += 1
            if len(r.overlap_tiers) > 1:
                ambiguous_count += 1
                if len(ambiguous_examples) < 20:
                    ambiguous_examples.append(
                        {
                            "designation": r.designation,
                            "grade": r.grade,
                            "tiers": r.overlap_tiers,
                        }
                    )
        elif tiers:
            # Matched no tier despite a configured scheme → no wallet. Bucket by
            # the designation that didn't match so the broker can act on it.
            ineligible_designations[r.designation or "(no job title)"] += 1

    tier_headcounts = [
        TierHeadcount(
            name=str(t.get("name") or f"Tier {i + 1}"),
            country=(str(t.get("country")) if t.get("country") else None),
            currency=(t.get("currency") or meta.get("currency") or DEFAULT_CURRENCY),
            eligible=tier_emp_counts.get(i, 0),
            by_family_status=dict(tier_fs_counts.get(i, {})),
            wallet_by_family_status={fs: tier_wallet(t, fs, meta) for fs in FAMILY_CODES},
        )
        for i, t in enumerate(tiers)
    ]

    return FlexMembership(
        employees_total=len(roster.resolved),
        family_status_counts=dict(fs_counts),
        source_counts=dict(source_counts),
        tiers=tier_headcounts,
        assignments=assignments,
        scheme_status=roster.scheme_status,
        ineligible_count=sum(ineligible_designations.values()),
        ineligible_designations=dict(ineligible_designations),
        ambiguous_count=ambiguous_count,
        ambiguous_examples=ambiguous_examples,
    )


# ── Coverage validation ("is anyone left out?") ───────────────────────────────
# Turns the resolved roster into a reconciliation the broker can trust: every
# active employee lands in exactly one of {ok, no-family-status, not-in-any-tier}
# (multiple-tiers is an assigned-but-review warning), and every active dependant
# in {classified, unclassified, orphaned, inactive-link}. Each exception carries
# the identity of WHO, so the broker can act (and download the full list).

# How many exception rows the JSON preview carries per bucket; the .xlsx export is
# always complete. Kept below the 200 list cap so the preview stays lightweight.
COVERAGE_PREVIEW_CAP = 100

# Employee exception buckets.
BUCKET_NO_FAMILY_STATUS = "no_family_status"
BUCKET_NOT_IN_ANY_TIER = "not_in_any_tier"
BUCKET_MULTIPLE_TIERS = "multiple_tiers"
# Dependant exception buckets.
BUCKET_UNCLASSIFIED_DEP = "unclassified_relationship"
BUCKET_OUTSIDE_AGE_DEP = "outside_age_window"
BUCKET_ORPHANED_DEP = "orphaned"
BUCKET_INACTIVE_LINK_DEP = "inactive_link"

_BUCKET_LABELS: dict[str, str] = {
    BUCKET_NO_FAMILY_STATUS: "No family status",
    BUCKET_NOT_IN_ANY_TIER: "Not in any tier",
    BUCKET_MULTIPLE_TIERS: "In multiple tiers",
    BUCKET_UNCLASSIFIED_DEP: "Unclassified relationship",
    BUCKET_OUTSIDE_AGE_DEP: "Outside age window",
    BUCKET_ORPHANED_DEP: "Orphaned (no employee link)",
    BUCKET_INACTIVE_LINK_DEP: "Linked to inactive employee",
}

# Dependant attribute keys that may carry the dependant's own name.
_DEP_NAME_KEYS: tuple[str, ...] = (
    "name", "full_name", "dependant_name", "dependent_name", "member_name",
)


@dataclass(frozen=True)
class CoverageRow:
    """One person behind an exception. ``label`` is the employee's designation for
    employee rows, or the dependant's name for dependant rows; ``detail`` is the
    human-readable reason / offending raw value."""

    staff_id: str | None
    name: str | None
    label: str | None
    detail: str


@dataclass(frozen=True)
class CoverageBucket:
    key: str
    label: str
    kind: str          # "employee" | "dependant"
    count: int
    rows: list[CoverageRow]   # full set at compute time; the API caps for preview
    truncated: bool = False


@dataclass
class FlexCoverage:
    employees_total: int
    employees_ok: int
    dependants_total: int
    dependants_ok: int
    has_tiers: bool
    scheme_status: str | None
    buckets: list[CoverageBucket]


def _dep_name(av: dict[str, Any]) -> str | None:
    for key in _DEP_NAME_KEYS:
        v = av.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _no_family_status_detail(r: ResolvedEmployee) -> str:
    if r.marital_raw:
        return f"unrecognized marital status: “{r.marital_raw}”"
    if r.dependant_count > 0:
        return (
            "has dependants but none is a spouse or child, and no marital status "
            "on the roster"
        )
    return "no dependants listed and no marital status on the roster"


def compute_flex_coverage(roster: ResolvedRoster) -> FlexCoverage:
    """Reconcile the resolved roster + dependant listing into coverage buckets."""
    has_tiers = bool(roster.tiers)

    no_status: list[CoverageRow] = []
    not_in_tier: list[CoverageRow] = []
    multi_tier: list[CoverageRow] = []
    employees_ok = 0

    for r in roster.resolved:
        resolved_status = r.family_status is not None
        assigned = (not has_tiers) or (r.tier_idx is not None)
        if resolved_status and assigned:
            employees_ok += 1

        if not resolved_status:
            no_status.append(
                CoverageRow(
                    staff_id=r.staff_id,
                    name=r.name,
                    label=r.designation,
                    detail=_no_family_status_detail(r),
                )
            )
        if has_tiers and r.tier_idx is None:
            not_in_tier.append(
                CoverageRow(
                    staff_id=r.staff_id,
                    name=r.name,
                    label=r.designation or "(no job title)",
                    detail=f"grade: {r.grade or '—'}",
                )
            )
        if len(r.overlap_tiers) > 1:
            multi_tier.append(
                CoverageRow(
                    staff_id=r.staff_id,
                    name=r.name,
                    label=r.designation,
                    detail="matches: " + " / ".join(r.overlap_tiers),
                )
            )

    # ── Dependant reconciliation ──
    unclassified: list[CoverageRow] = []
    outside_age: list[CoverageRow] = []
    orphaned: list[CoverageRow] = []
    inactive_link: list[CoverageRow] = []
    dependants_ok = 0

    for dep in roster.dependants:
        av = dep.attribute_values or {}
        rel_raw = str(av.get("relationship") or av.get("relation") or "").strip()
        kind = classify_relationship(rel_raw)
        dep_name = _dep_name(av)

        # Dependant rows keep a consistent shape: ``label`` is always the
        # dependant's own name; ``name``/``staff_id`` identify the linked employee
        # (None when there isn't one).
        rel_note = f" (relationship “{rel_raw}”)" if rel_raw else ""
        if dep.employee_id is None:
            orphaned.append(
                CoverageRow(
                    staff_id=None,
                    name=None,
                    label=dep_name,
                    detail=f"not linked to any employee{rel_note}",
                )
            )
        elif dep.employee_id not in roster.active_emp_ids:
            inactive_link.append(
                CoverageRow(
                    staff_id=None,
                    name=None,
                    label=dep_name,
                    detail=f"linked to an employee not on the active roster{rel_note}",
                )
            )
        elif kind in ("spouse", "child"):
            # A classified spouse/child outside the scheme's age window draws no
            # coverage and no flex — surface it here so this view matches what the
            # benefit statement / pricing actually cover (and the wallet sizing).
            if roster.ref is not None and not _dependant_eligible(
                dep, roster.age_limits, roster.ref
            ):
                emp = roster.emp_by_id.get(dep.employee_id)
                win = roster.age_limits.get(kind) or {}
                outside_age.append(
                    CoverageRow(
                        staff_id=emp.staff_id if emp else None,
                        name=emp.employee_name if emp else None,
                        label=dep_name,
                        detail=(
                            f"{kind} outside the eligible age window "
                            f"(max {win.get('max', '—')}, age next-birthday)"
                        ),
                    )
                )
            else:
                dependants_ok += 1
        else:
            emp = roster.emp_by_id.get(dep.employee_id)
            unclassified.append(
                CoverageRow(
                    staff_id=emp.staff_id if emp else None,
                    name=emp.employee_name if emp else None,
                    label=dep_name,
                    detail=(
                        f"relationship “{rel_raw}” not recognized as spouse/child"
                        if rel_raw
                        else "no relationship on the dependant record"
                    ),
                )
            )

    def bucket(key: str, kind: str, rows: list[CoverageRow]) -> CoverageBucket:
        return CoverageBucket(
            key=key, label=_BUCKET_LABELS[key], kind=kind, count=len(rows), rows=rows
        )

    buckets: list[CoverageBucket] = [
        bucket(BUCKET_NO_FAMILY_STATUS, "employee", no_status),
    ]
    if has_tiers:
        buckets.append(bucket(BUCKET_NOT_IN_ANY_TIER, "employee", not_in_tier))
        buckets.append(bucket(BUCKET_MULTIPLE_TIERS, "employee", multi_tier))
    buckets.append(bucket(BUCKET_UNCLASSIFIED_DEP, "dependant", unclassified))
    buckets.append(bucket(BUCKET_OUTSIDE_AGE_DEP, "dependant", outside_age))
    buckets.append(bucket(BUCKET_ORPHANED_DEP, "dependant", orphaned))
    buckets.append(bucket(BUCKET_INACTIVE_LINK_DEP, "dependant", inactive_link))

    return FlexCoverage(
        employees_total=len(roster.resolved),
        employees_ok=employees_ok,
        dependants_total=len(roster.dependants),
        dependants_ok=dependants_ok,
        has_tiers=has_tiers,
        scheme_status=roster.scheme_status,
        buckets=buckets,
    )


def cap_bucket(b: CoverageBucket, cap: int = COVERAGE_PREVIEW_CAP) -> CoverageBucket:
    """A copy of ``b`` with rows capped to ``cap`` (count/label preserved)."""
    if len(b.rows) <= cap:
        return b
    return CoverageBucket(
        key=b.key, label=b.label, kind=b.kind, count=b.count,
        rows=b.rows[:cap], truncated=True,
    )


# ── Roster vocabulary (powers the tier match-set dropdowns) ───────────────────


@dataclass(frozen=True)
class VocabValue:
    value: str    # representative raw value as it appears on the roster
    count: int    # active employees carrying it
    claimed: bool # already selected by some tier's match set (coverage signal)


@dataclass(frozen=True)
class RosterVocab:
    """Distinct employee-type (designation) and job-grade values actually present
    on the active roster — the authoritative vocabulary a broker picks from when
    reconciling flex tiers. ``claimed`` flags values no tier yet selects."""

    employees_total: int
    designations: list[VocabValue]
    grades: list[VocabValue]


def _tally(bucket: dict[str, dict[str, Any]], raw: object) -> None:
    s = str(raw or "").strip()
    if not s:
        return
    norm = _normalize_label(s)
    if not norm:
        return
    slot = bucket.setdefault(norm, {"value": s, "count": 0})
    slot["count"] += 1


def _vocab_list(bucket: dict[str, dict[str, Any]], covered: set[str]) -> list[VocabValue]:
    return [
        VocabValue(value=slot["value"], count=slot["count"], claimed=norm in covered)
        for norm, slot in sorted(bucket.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    ]


def roster_vocabulary(
    db: Session, policy_year_id: str, client_id: str | None
) -> RosterVocab:
    """Collect the distinct designation + grade values across the active roster,
    flagging which are already claimed by a tier's reconciled match sets."""
    scheme_row = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    tiers = [
        t for t in ((scheme_row.scheme or {}).get("tiers") or []) if isinstance(t, dict)
    ] if scheme_row else []
    covered_grades: set[str] = set()
    covered_desigs: set[str] = set()
    for t in tiers:
        g, d = _tier_match_sets(t)
        covered_grades |= g
        covered_desigs |= d

    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year_id,
                Employee.status == "active",
            )
        ).scalars()
    )

    desig_bucket: dict[str, dict[str, Any]] = {}
    grade_bucket: dict[str, dict[str, Any]] = {}
    for emp in employees:
        raw_attrs = emp.attribute_values or {}
        derived = derive(raw_attrs, schemas)
        _, grade_str, designation = employee_signals(derived, raw_attrs)
        _tally(desig_bucket, designation)
        _tally(grade_bucket, grade_str)

    return RosterVocab(
        employees_total=len(employees),
        designations=_vocab_list(desig_bucket, covered_desigs),
        grades=_vocab_list(grade_bucket, covered_grades),
    )
