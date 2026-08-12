"""Detect lives covered twice because two employees of the same company are family.

When a child's father and mother both work here, the child can be listed as a
dependant under each of them — two premium lines, two flex family wallets, two
panel cards, and claims payable from both sides. The same applies to an employee
who is also carried as a colleague's spouse.

This is invisible to the existing machinery ON PURPOSE:
``roster_dedup.dependant_candidate_keys`` suppresses employee-agnostic name+DOB
matching for linked rows "so linked dependants of different employees never
false-match" — exactly the signal wanted here. So this module reads the same
identity parts with the opposite intent rather than reusing that path.

Computed on READ, never materialized: the roster moves constantly and a stored
flag would go stale. The only thing persisted is the broker's DECISION
(``models/dual_coverage_decision``).

Two outputs, and keeping them apart is the whole design:

- **Cases** — a life genuinely listed more than once. These carry the count, the
  banner and the decision workflow.
- **Opportunities** — married colleagues whose child is listed under only one of
  them. Real, and what makes a dual-coverage *option* visible, but it is the
  NORMAL state of such a family: on a roster with 40 dual-employee couples it
  would produce ~80 rows and bury the handful of actual duplicates. It is
  reported separately and never counted into the alert.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Dependant, Employee, PolicyYear, Product
from app.services.benefit_statement import _category_covers_dependants
from app.services.flex_membership import (
    ResolvedRoster,
    classify_relationship,
    resolve_roster,
)
from app.services.plan_hydration import hydrate_plans
from app.services.roster_attributes import (
    DOB_KEYS,
    REL_KEYS,
    first_value,
    mask_nric,
    parse_dob,
)
from app.services.roster_dedup import DEP_NAME_KEYS, dependant_nric, employee_nric

# Cases shown in the JSON payload. The count is always exact; the list is capped
# like every other reconciliation surface (`flex_membership.COVERAGE_PREVIEW_CAP`).
PREVIEW_CAP = 100

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize_name(raw: object) -> str:
    """The ONE name normalization used for matching and for the stored key.

    Three already exist in this codebase and they disagree — ``dependants.py``
    lowercases only, ``flex_membership._normalize_label`` folds punctuation,
    ``roster_parser._normalize_name`` collapses commas — so "Tan, Ah Kow" and
    "Tan Ah Kow" match under one and not the others.

    **This function is load-bearing and must not be changed casually.** It feeds
    ``life_key``, which is stored on every recorded decision; changing it
    re-keys every case and orphans the decisions taken against them.
    """
    text = _PUNCT.sub(" ", str(raw or ""))
    return _WS.sub(" ", text).strip().casefold()


def _dob_key(attrs: dict) -> str:
    """ISO date of birth, or "" when it cannot be parsed.

    ``parse_dob`` and ``iso_date`` disagree on unparseable input — the first
    returns None, the second passes the raw text through (``_DATE_FORMATS`` has
    no dot format, so "15.03.1990" splits them). Matching uses the strict one, so
    an unparseable date simply never matches rather than matching only itself.
    """
    parsed = parse_dob(first_value(attrs, DOB_KEYS))
    return parsed.isoformat() if parsed else ""


@dataclass(frozen=True)
class Identity:
    """What is known about one human, from one row."""

    nric: str
    name: str
    dob: str

    @property
    def keys(self) -> list[str]:
        """Every candidate key this row could be matched on, strongest first.

        A decision matches a case when ANY key overlaps, which is what survives
        the workflow's own success: the broker reviews a name+DOB case, fills in
        the missing NRIC, and the identity gains an ``nric:`` key it did not have.
        """
        out: list[str] = []
        if self.nric:
            out.append(f"nric:{self.nric}")
        # Never name alone — two unrelated children can share a name.
        if self.name and self.dob:
            out.append(f"sig:{self.name}|{self.dob}")
        return out


def dependant_identity(dep: Dependant) -> Identity:
    attrs = dep.attribute_values or {}
    return Identity(
        # The column is not written by the portal self-add path
        # (`portal_dependants.py`) and approval does not backfill it, so the
        # attribute fallback is what makes portal-added dependants visible at
        # all — and they are the highest-risk population, since that path does
        # no dedup whatsoever.
        nric=dep.national_id_normalized or dependant_nric(attrs) or "",
        # DEP_NAME_KEYS, never NAME_KEYS: the latter includes `employee_name`,
        # which dependant rows genuinely carry, so a row with no dependant_name
        # would be matched under the PARENT's name.
        name=normalize_name(first_value(attrs, DEP_NAME_KEYS)),
        dob=_dob_key(attrs),
    )


def employee_identity(emp: Employee) -> Identity:
    attrs = emp.attribute_values or {}
    return Identity(
        nric=emp.national_id_normalized or employee_nric(attrs) or "",
        name=normalize_name(emp.employee_name),
        dob=_dob_key(attrs),
    )


def life_key(identities: list[Identity]) -> str:
    """The stored key for a life, hashed.

    Opaque rather than readable for two reasons. It goes in a URL path, and a
    readable one would be ``sig:tan ah kow|1990-03-15`` — a full name and date of
    birth in access logs, proxy logs and browser history, while every other field
    in this module's payloads is masked. It also contains ``|``, spaces and
    potentially ``/``, which breaks path routing outright.

    The NRIC is picked with ``min(sorted(...))``, never "whichever row had one":
    no dependant loader has an ORDER BY, so heap order can differ between two
    requests, and two rows may carry DIFFERENT NRICs for one life — which is a
    case tier-2 matching exists to catch.
    """
    nrics = sorted({i.nric for i in identities if i.nric})
    if nrics:
        raw = f"nric:{nrics[0]}"
    else:
        sigs = sorted({f"{i.name}|{i.dob}" for i in identities if i.name and i.dob})
        raw = f"sig:{sigs[0]}" if sigs else "sig:"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass
class Party:
    """One side of a shared life: an employee, and how the life reaches them."""

    employee_id: str | None
    staff_id: str
    employee_name: str | None
    # The dependant row under this employee, when there is one. None means the
    # party IS the life (an employee also carried as someone's spouse), or a
    # co-parent who could cover but does not.
    dependant_id: str | None
    relationship: str | None
    covered: bool = False
    covered_products: list[str] = field(default_factory=list)
    # A row nobody can attribute — reported as evidence, never as coverage.
    unlinked: bool = False


@dataclass
class Case:
    life_key: str
    life_keys: list[str]
    name: str
    dob: str
    nric_masked: str | None
    relationship: str | None
    match_tier: str  # "nric" | "name_dob"
    flags: list[str]  # "listed_twice" | "employee_as_spouse"
    parties: list[Party]
    overlapping_products: list[str]
    severity: str  # "warn" | "info"
    parties_digest: str


@dataclass
class Opportunity:
    """Married colleagues, child listed under only one of them."""

    couple_key: str
    employees: list[Party]
    child_name: str
    child_dob: str
    listed_under_staff_id: str
    other_staff_id: str


def _identity_index(identities: dict[str, Identity]) -> dict[str, list[str]]:
    """key → owner ids. Ambiguity is the caller's problem to reject."""
    out: dict[str, list[str]] = {}
    for owner_id, ident in identities.items():
        for key in ident.keys:
            out.setdefault(key, []).append(owner_id)
    return out


def _digest(parties: list[Party]) -> str:
    """Fingerprint of WHO is in the family, and nothing else.

    Deliberately excludes ``covered`` / ``covered_products``: those move on any
    unrelated plan edit, GST change or category re-match, and including them
    would re-surface every recorded decision constantly. Row UUIDs are excluded
    for the same class of reason — they are year-scoped and any delete+recreate
    would invalidate every decision — so this hashes the stable staff-id +
    relationship pairs instead. Sorted, so ordering cannot move the hash.
    """
    payload = sorted(
        [p.staff_id or "", (p.relationship or "").casefold(), "1" if p.dependant_id else "0"]
        for p in parties
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]


def _party_for(roster: ResolvedRoster, dep: Dependant) -> Party:
    emp = roster.emp_by_id.get(dep.employee_id or "")
    attrs = dep.attribute_values or {}
    return Party(
        employee_id=emp.id if emp else None,
        staff_id=(emp.staff_id if emp else "") or "",
        employee_name=emp.employee_name if emp else None,
        dependant_id=dep.id,
        relationship=str(first_value(attrs, REL_KEYS) or "") or None,
        unlinked=emp is None,
    )


def _employee_key_index(roster: ResolvedRoster) -> dict[str, str]:
    """key → employee id, with AMBIGUOUS keys dropped.

    ``dependants.py::_employee_link_indexes`` establishes the rule: a name that
    resolves to more than one employee must never link, because the winner is
    whichever won a dict race. The same applies here — and more sharply, since a
    bad couple link fabricates a whole family.
    """
    idents = {e.id: employee_identity(e) for e in roster.emp_by_id.values()}
    index = _identity_index(idents)
    return {key: owners[0] for key, owners in index.items() if len(owners) == 1}


def _group_lives(roster: ResolvedRoster) -> list[tuple[list[Dependant], list[Identity]]]:
    """Dependant rows grouped into the LIVES they describe.

    One life may be reachable under both an ``nric:`` key and a ``sig:`` key, and
    a later row carrying both bridges two groups that were opened separately — so
    the groups are merged rather than left split, which would report one
    duplicate as two half-cases.
    """
    owner: dict[str, int] = {}
    rows: dict[int, list[Dependant]] = {}
    idents: dict[int, list[Identity]] = {}
    next_id = 0

    for dep in roster.dependants:
        ident = dependant_identity(dep)
        if not ident.keys:
            continue  # nothing identifiable — never guess
        hits = sorted({owner[k] for k in ident.keys if k in owner})
        if not hits:
            gid = next_id
            next_id += 1
        else:
            gid = hits[0]
            for stale in hits[1:]:
                rows[gid].extend(rows.pop(stale, []))
                idents[gid].extend(idents.pop(stale, []))
                for key, value in list(owner.items()):
                    if value == stale:
                        owner[key] = gid
        for key in ident.keys:
            owner[key] = gid
        rows.setdefault(gid, []).append(dep)
        idents.setdefault(gid, []).append(ident)

    return [(rows[g], idents[g]) for g in sorted(rows)]


# ── Coverage: who is actually being paid for ────────────────────────────────


def coverage_by_employee(
    db: Session,
    py: PolicyYear,
    roster: ResolvedRoster,
    employee_ids: set[str],
    covers_dependants: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, set[str]]]:
    """``{employee_id: {product_code: covered dependant ids}}``.

    Batched over the PARTY employees only — a handful, not the roster — and
    through ``hydrate_plans``, which already drops DECLINED lines before any
    dependant logic runs. That detail is load-bearing: ``resolve_plan`` returns
    ``covered_dependant_ids=None`` for a declined override *as well as* for "no
    opinion", so resolving overrides directly would apply the cohort-default
    sweep to a declined employee and report them as covering their spouse.
    """
    if covers_dependants is None:
        covers_dependants = {}
    employees = [roster.emp_by_id[i] for i in sorted(employee_ids) if i in roster.emp_by_id]
    if not employees:
        return {}
    plans = hydrate_plans(employees, db, py.id)

    # `_category_covers_dependants` needs product/category fields `MatchedPlan`
    # does not carry, so one bulk query for the categories in play.
    cat_ids = {mp.category_id for rows in plans.values() for mp in rows if mp.category_id}
    cat_info: dict[str, tuple[bool, dict | None, str | None, str | None]] = {}
    if cat_ids:
        for cat, has_dep in db.execute(
            select(Category, Product.has_dependants)
            .join(Product, Category.product_id == Product.id)
            .where(Category.id.in_(cat_ids))
        ).all():
            cat_info[cat.id] = (
                bool(has_dep),
                cat.plan_assignments,
                cat.display_name,
                cat.raw_description,
            )

    deps_by_emp: dict[str, list[Dependant]] = {}
    for dep in roster.dependants:
        if dep.employee_id:
            deps_by_emp.setdefault(dep.employee_id, []).append(dep)

    out: dict[str, dict[str, set[str]]] = {}
    for emp_id, matched in plans.items():
        per_product: dict[str, set[str]] = {}
        own = deps_by_emp.get(emp_id, [])
        for mp in matched:
            info = cat_info.get(mp.category_id or "")
            # Whether this product's cover EXTENDS to dependants at all, read
            # from the cohort and never from the current selection. The two are
            # different questions: an override listing nobody means "this
            # employee covers no dependant here", not "this product cannot".
            # Only the second may decide where a dependant can be ADDED, and
            # conflating them let a restore put a child on every product the
            # employee held — group term life included — after a drop had
            # removed them from the three that actually carry dependants.
            covers = (
                _category_covers_dependants(bool(own), info[1], info[2], info[3])
                if info
                else False
            )
            covers_dependants.setdefault(emp_id, set())
            if covers:
                covers_dependants[emp_id].add(mp.product_code)
            if mp.covered_dependant_ids is not None:
                per_product[mp.product_code] = set(mp.covered_dependant_ids)
                continue
            # No explicit election: the cohort heuristic sweeps in every active
            # dependant of that employee, exactly as the benefit statement does.
            per_product[mp.product_code] = {d.id for d in own} if covers else set()
        out[emp_id] = per_product
    return out


def _stamp_coverage(
    parties: list[Party], coverage: dict[str, dict[str, set[str]]]
) -> list[str]:
    """Fill in each party's coverage; return the products they OVERLAP on.

    Intersected, never unioned. One employee covering the child under GHS while
    the other's cohort covers dependants under GPA is not paying twice for the
    same thing, and reporting it as such would make the warning meaningless.
    """
    covered_sets: list[set[str]] = []
    for party in parties:
        products: set[str] = set()
        if not party.unlinked and party.employee_id:
            for code, ids in (coverage.get(party.employee_id) or {}).items():
                if party.dependant_id is None:
                    products.add(code)  # the party IS the life: their own cover
                elif party.dependant_id in ids:
                    products.add(code)
        party.covered_products = sorted(products)
        party.covered = bool(products)
        if party.covered:
            covered_sets.append(products)
    if len(covered_sets) < 2:
        return []
    return sorted(set.intersection(*covered_sets))


# ── Detection ───────────────────────────────────────────────────────────────


@dataclass
class DualCoverage:
    cases: list[Case]
    opportunities: list[Opportunity]


def duplicated_dependant_ids(db: Session, py: PolicyYear) -> set[str]:
    """Dependant rows that are the SAME life as a row under another employee.

    The cheap half of :func:`detect` — grouping only, with no coverage
    enrichment — for callers that just need "is this life doubled" and not who
    pays for it. Shares ``_group_lives``, so it can never disagree with the
    cases the review sheet shows.
    """
    roster = resolve_roster(db, py.id, py.client_id, with_dependant_detail=True)
    out: set[str] = set()
    for deps, _idents in _group_lives(roster):
        sponsors = {
            d.employee_id
            for d in deps
            if d.employee_id and d.employee_id in roster.emp_by_id
        }
        if len(sponsors) > 1:
            out.update(d.id for d in deps)
    return out


def detect(db: Session, py: PolicyYear) -> DualCoverage:
    """Every case and opportunity in the benefit year, computed fresh."""
    roster = resolve_roster(db, py.id, py.client_id, with_dependant_detail=True)
    emp_by_key = _employee_key_index(roster)
    lives = _group_lives(roster)

    cases: list[Case] = []
    for deps, idents in lives:
        # Sponsors that are ACTIVE employees. `Dependant.employee_id` is
        # ON DELETE SET NULL and a terminated employee keeps their links, so a
        # non-null id is not "active sponsor" — `emp_by_id` holds active only.
        sponsors = {
            d.employee_id
            for d in deps
            if d.employee_id and d.employee_id in roster.emp_by_id
        }
        listed_twice = len(sponsors) > 1

        # Is this life ALSO an employee, carried as somebody's spouse? Then they
        # hold their own cover and spouse cover at once.
        self_emp_id: str | None = None
        for dep, ident in zip(deps, idents, strict=True):
            rel = classify_relationship(first_value(dep.attribute_values or {}, REL_KEYS))
            if rel != "spouse":
                continue
            hit = next((emp_by_key[k] for k in ident.keys if k in emp_by_key), None)
            if hit and hit != dep.employee_id:
                self_emp_id = hit
                break

        if not listed_twice and self_emp_id is None:
            continue

        flags: list[str] = []
        if listed_twice:
            flags.append("listed_twice")
        if self_emp_id is not None:
            flags.append("employee_as_spouse")

        parties = [_party_for(roster, d) for d in deps]
        if self_emp_id is not None:
            emp = roster.emp_by_id[self_emp_id]
            parties.insert(
                0,
                Party(
                    employee_id=emp.id,
                    staff_id=emp.staff_id or "",
                    employee_name=emp.employee_name,
                    dependant_id=None,
                    relationship="employee",
                ),
            )

        head = idents[0]
        attrs = deps[0].attribute_values or {}
        nrics = sorted({i.nric for i in idents if i.nric})
        cases.append(
            Case(
                life_key=life_key(idents),
                life_keys=sorted({k for i in idents for k in i.keys}),
                name=str(first_value(attrs, DEP_NAME_KEYS) or "").strip(),
                dob=head.dob,
                nric_masked=mask_nric(nrics[0]) if nrics else None,
                relationship=str(first_value(attrs, REL_KEYS) or "") or None,
                match_tier="nric" if nrics else "name_dob",
                flags=flags,
                parties=parties,
                overlapping_products=[],
                severity="info",
                parties_digest=_digest(parties),
            )
        )

    coverage = coverage_by_employee(
        db, py, roster, {p.employee_id for c in cases for p in c.parties if p.employee_id}
    )
    for case in cases:
        case.overlapping_products = _stamp_coverage(case.parties, coverage)
        # Severity keys on PRODUCT OVERLAP, not on "is covered". The cohort
        # heuristic marks nearly every party covered, and an employee-as-spouse
        # is trivially covered under their own cohort — so a covered-count rule
        # collapses to warn-on-everything and stops being a signal at all.
        case.severity = "warn" if case.overlapping_products else "info"

    cases.sort(key=lambda c: (c.severity != "warn", c.name, c.life_key))
    return DualCoverage(
        cases=cases, opportunities=_opportunities(roster, emp_by_key, cases)
    )


def _opportunities(
    roster: ResolvedRoster, emp_by_key: dict[str, str], cases: list[Case]
) -> list[Opportunity]:
    """Married colleagues whose child is listed under only one of them."""
    # Canonical couple links: A and B usually list EACH OTHER, so the link is
    # discovered twice. Keying on the sorted pair makes one couple one link, and
    # therefore one row per child instead of two.
    couples: dict[tuple[str, str], None] = {}
    for dep in roster.dependants:
        if classify_relationship(first_value(dep.attribute_values or {}, REL_KEYS)) != "spouse":
            continue
        sponsor_id = dep.employee_id
        if not sponsor_id or sponsor_id not in roster.emp_by_id:
            continue
        ident = dependant_identity(dep)
        other = next((emp_by_key[k] for k in ident.keys if k in emp_by_key), None)
        if not other or other == sponsor_id:
            continue
        couples[tuple(sorted((sponsor_id, other)))] = None  # type: ignore[index]

    deps_by_emp: dict[str, list[Dependant]] = {}
    for dep in roster.dependants:
        if dep.employee_id:
            deps_by_emp.setdefault(dep.employee_id, []).append(dep)

    # A life already reported as a CASE is not an opportunity — it is a duplicate.
    case_keys = {k for c in cases for k in c.life_keys}

    out: list[Opportunity] = []
    for pair in sorted(couples):
        a, b = (roster.emp_by_id[pair[0]], roster.emp_by_id[pair[1]])
        for holder, other in ((a, b), (b, a)):
            for dep in deps_by_emp.get(holder.id, []):
                attrs = dep.attribute_values or {}
                if classify_relationship(first_value(attrs, REL_KEYS)) != "child":
                    continue
                ident = dependant_identity(dep)
                if any(k in case_keys for k in ident.keys):
                    continue
                out.append(
                    Opportunity(
                        couple_key=hashlib.sha256(
                            f"couple:{pair[0]}|{pair[1]}".encode()
                        ).hexdigest()[:32],
                        employees=[
                            Party(
                                holder.id, holder.staff_id or "",
                                holder.employee_name, None, "employee",
                            ),
                            Party(
                                other.id, other.staff_id or "",
                                other.employee_name, None, "employee",
                            ),
                        ],
                        child_name=str(first_value(attrs, DEP_NAME_KEYS) or "").strip(),
                        child_dob=ident.dob,
                        listed_under_staff_id=holder.staff_id or "",
                        other_staff_id=other.staff_id or "",
                    )
                )
    return sorted(out, key=lambda o: (o.listed_under_staff_id, o.child_name))
