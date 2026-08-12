"""Legal-entity vocabulary for the Insured picker.

A category's ``plan_assignments.insured`` gates matching: it only matches
employees whose roster ``attribute_values["entity"]`` is one of the named
entities (`matching_engine.category_insured_entities`). Both sides are free
text, so the two can silently disagree and the employee just lands unmatched.

This module supplies the vocabulary that makes the picker pick *matching*
values. Two groups, deliberately kept apart:

* ``roster`` — entities actually present on the active roster, with headcounts.
  Picking one of these guarantees the gate lets those employees through.
* ``known`` — entities already named somewhere in the configuration (a
  category's insured list, or the slip header's Insured field) that match NO
  roster entity. These are what the broker must reconcile: usually the slip's
  legal spelling against the roster's shorthand.

Product setup often happens BEFORE the roster is uploaded, so ``roster`` is
legitimately empty at that point — the picker must still allow free entry, and
the reconciliation check has to re-run once the roster lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import MAX_LIMIT
from app.models import Category, Employee, PolicyYear, Product, ProductSetup
from app.services.matching_engine import (
    EntityAliases,
    entity_alias_map,
    insured_names,
    jaccard,
    normalize_entity,
    product_entities,
    resolve_entities,
    tokenize,
)


@dataclass(frozen=True)
class EntityValue:
    value: str  # representative raw spelling
    count: int  # active employees carrying it (0 for config-only entities)
    claimed: bool  # already named by at least one category's insured list
    # For a `known` (unreconciled) entity: the roster spelling it most likely
    # means, so the UI can offer a one-click alias. None when nothing is close.
    suggestion: str | None = None


@dataclass(frozen=True)
class EntityVocab:
    employees_total: int
    # Entities on the roster — safe to pick, they match `count` employees.
    roster: list[EntityValue]
    # Entities named in the config that match no roster entity.
    known: list[EntityValue]


def _tally(
    bucket: dict[frozenset[str], dict[str, Any]], raw: object, aliases: EntityAliases
) -> None:
    s = str(raw or "").strip()
    if not s:
        return
    # Resolve through the alias map, exactly as the gate does — otherwise an
    # entity already bridged by an alias would be reported as unreconciled.
    # An alias may expand to SEVERAL entities, so the bucket is keyed by the
    # whole resolved set; two roster spellings that resolve to the same set
    # (e.g. an abbreviation and its expansion) collapse into one row.
    keys = resolve_entities(s, aliases)
    if not keys:
        return
    slot = bucket.setdefault(keys, {"value": s, "count": 0})
    slot["count"] += 1


# Corporate-suffix tokens carry no identity — dropped before deriving initials
# so "City Serviced Offices Pte Ltd" yields "cso", not "csopl".
_SUFFIX_TOKENS = frozenset({"pte", "ltd", "inc", "corp", "co", "holdings", "group"})


def _acronym(norm: str) -> str:
    """Initials of the identity-bearing tokens: "city serviced offices pte ltd"
    → "cso"."""
    return "".join(t[0] for t in norm.split() if t and t not in _SUFFIX_TOKENS)


@dataclass(frozen=True)
class _Candidate:
    """A roster entity pre-indexed for suggestion matching. Built ONCE per
    request — `_closest` runs per unreconciled name, so tokenizing inside its
    loop would re-tokenize the whole roster for each one."""

    value: str
    tokens: frozenset[str]
    acronym: str


def _identity_tokens(norm: str) -> frozenset[str]:
    """Tokens that actually identify the company.

    Corporate suffixes are dropped: almost every Singapore entity ends in
    "Pte Ltd", so counting them makes any two names look ~40% similar and the
    UI proposes a nonsense alias between unrelated companies.
    """
    return frozenset(t for t in tokenize(norm) if t not in _SUFFIX_TOKENS)


def _index_candidates(bucket: dict[Any, dict[str, Any]]) -> list[_Candidate]:
    # Suggestions match against a roster entity's OWN spelling (an unreconciled
    # config name has no alias yet), so index by the representative value's
    # normalized form rather than the alias-resolved bucket key.
    out: list[_Candidate] = []
    for slot in bucket.values():
        norm = normalize_entity(slot["value"])
        out.append(
            _Candidate(
                value=slot["value"],
                tokens=_identity_tokens(norm),
                acronym=_acronym(norm),
            )
        )
    return out


def _closest(norm: str, candidates: list[_Candidate]) -> str | None:
    """The roster entity a config-only name most likely means.

    Two signals, because the two failure modes look nothing alike:

    * **Acronym** — the dominant case. "CSO" shares NO words with "City Serviced
      Offices Pte Ltd", so token overlap alone finds nothing; comparing against
      the roster name's initials does.
    * **Token overlap** (`jaccard`, the measure the fuzzy match tier uses) — for
      partial or reordered names that do share words.

    An unrelated name matches neither and yields no suggestion, so the UI never
    proposes a nonsense alias.
    """
    target = set(_identity_tokens(norm))
    if not target:
        return None
    target_acronym = norm.replace(" ", "")
    best, best_score = None, 0.0
    for cand in candidates:
        # An exact acronym hit outranks any partial word overlap.
        if target_acronym and cand.acronym == target_acronym:
            return cand.value
        score = jaccard(target, set(cand.tokens))
        if score > best_score:
            best, best_score = cand.value, score
    return best if best_score > 0 else None


def _gating_entities(
    db: Session, policy_year_id: str, aliases: EntityAliases
) -> dict[str, str]:
    """{resolved: raw spelling} for every entity that actually GATES matching.

    Must cover BOTH sources, in the same precedence the matcher applies
    (`_build_product_indices`): a product's own `product_metadata["entities"]`
    when set, otherwise each of its categories' `plan_assignments["insured"]`.
    Reading only the category side would mark a roster entity "unclaimed" while
    a product gate is actively excluding everyone else — which is precisely the
    silent exclusion the reconciliation panel exists to surface.
    """
    out: dict[str, str] = {}

    def _add(raw_value: object) -> None:
        for name in insured_names(raw_value):
            # An aliased entity may expand to several — record every one, so a
            # roster spelling covering any of them counts as claimed.
            for norm in resolve_entities(name, aliases):
                out.setdefault(norm, name)

    cats = list(
        db.execute(
            select(Category.product_id, Category.plan_assignments).where(
                Category.policy_year_id == policy_year_id
            )
        ).all()
    )
    product_ids = {pid for pid, _ in cats if pid}
    products = (
        {
            p.id: p
            for p in db.execute(
                select(Product).where(Product.id.in_(product_ids))
            ).scalars()
        }
        if product_ids
        else {}
    )
    for product_id, pa in cats:
        gate = product_entities(products.get(product_id), aliases)
        if gate:
            # Product-level field wins — record its RAW spellings, not the
            # normalized set, so the UI can show what the broker typed.
            meta = products[product_id].product_metadata or {}
            _add(meta.get("entities"))
        elif isinstance(pa, dict):
            _add(pa.get("insured"))
    return out


def _setup_header_entities(
    db: Session, policy_year_id: str, aliases: EntityAliases
) -> dict[str, str]:
    """{normalized: raw} from each product setup's header.

    Covers the descriptive `insured` wording AND the picked `entities` — the
    latter because a DRAFT setup has entities the broker chose but hasn't
    confirmed onto the product yet, and those are worth reconciling before they
    become the live gate.
    """
    out: dict[str, str] = {}
    rows = db.execute(
        select(ProductSetup.answers).where(
            ProductSetup.policy_year_id == policy_year_id
        )
    ).scalars()
    for answers in rows:
        if not isinstance(answers, dict):
            continue
        header = answers.get("header")
        if not isinstance(header, dict):
            continue
        for key in ("insured", "entities"):
            for name in insured_names(header.get(key)):
                for norm in resolve_entities(name, aliases):
                    out.setdefault(norm, name)
    return out


def entity_vocabulary(db: Session, policy_year: PolicyYear) -> EntityVocab:
    """Roster entities (with headcounts) plus config-only entities that match
    no roster value."""
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year.id,
                Employee.status == "active",
            )
        ).scalars()
    )
    aliases = entity_alias_map(db, policy_year.client_id)
    roster_bucket: dict[frozenset[str], dict[str, Any]] = {}
    for emp in employees:
        _tally(roster_bucket, (emp.attribute_values or {}).get("entity"), aliases)

    claimed = _gating_entities(db, policy_year.id, aliases)
    claimed_norms = set(claimed)
    configured = {
        **_setup_header_entities(db, policy_year.id, aliases),
        **claimed,
    }
    # Flat set of every entity any roster spelling resolves to — a config
    # entity is reconciled (out of `known`) when SOME roster row covers it.
    roster_norms: set[str] = set().union(*roster_bucket.keys()) if roster_bucket else set()

    # Both lists are capped: a roster with dirty free-text entity data can hold
    # thousands of distinct spellings, and every picker/panel refetches this.
    # Roster is sorted by headcount so the cap drops the long tail, not the
    # entities that matter. A roster row is claimed when ANY entity it resolves
    # to is named by a category.
    roster = [
        EntityValue(
            value=slot["value"],
            count=slot["count"],
            claimed=not keys.isdisjoint(claimed_norms),
        )
        for keys, slot in sorted(
            roster_bucket.items(), key=lambda kv: (-kv[1]["count"], kv[1]["value"])
        )
    ][:MAX_LIMIT]
    candidates = _index_candidates(roster_bucket)
    # De-dup by display spelling: a config cell that is itself an alias expands
    # to several norms all pointing at the SAME raw name, which would otherwise
    # list that one name once per entity it stands for.
    known: list[EntityValue] = []
    seen_values: set[str] = set()
    for norm, raw in sorted(configured.items()):
        if norm in roster_norms or raw in seen_values:
            continue
        seen_values.add(raw)
        known.append(
            EntityValue(
                value=raw,
                count=0,
                claimed=norm in claimed_norms,
                suggestion=_closest(norm, candidates),
            )
        )
        if len(known) >= MAX_LIMIT:
            break
    return EntityVocab(
        employees_total=len(employees), roster=roster, known=known
    )
