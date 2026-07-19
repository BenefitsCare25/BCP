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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, PolicyYear, ProductSetup
from app.services.matching_engine import insured_names, normalize_entity


@dataclass(frozen=True)
class EntityValue:
    value: str  # representative raw spelling
    count: int  # active employees carrying it (0 for config-only entities)
    claimed: bool  # already named by at least one category's insured list


@dataclass(frozen=True)
class EntityVocab:
    employees_total: int
    # Entities on the roster — safe to pick, they match `count` employees.
    roster: list[EntityValue]
    # Entities named in the config that match no roster entity.
    known: list[EntityValue]


def _tally(bucket: dict[str, dict], raw: object) -> None:
    s = str(raw or "").strip()
    if not s:
        return
    norm = normalize_entity(s)
    if not norm:
        return
    slot = bucket.setdefault(norm, {"value": s, "count": 0})
    slot["count"] += 1


def _category_entities(db: Session, policy_year_id: str) -> dict[str, str]:
    """{normalized: raw spelling} for every entity named by a category."""
    out: dict[str, str] = {}
    rows = db.execute(
        select(Category.plan_assignments).where(
            Category.policy_year_id == policy_year_id
        )
    ).scalars()
    for pa in rows:
        if not isinstance(pa, dict):
            continue
        for name in insured_names(pa.get("insured")):
            if norm := normalize_entity(name):
                out.setdefault(norm, name)
    return out


def _setup_header_entities(db: Session, policy_year_id: str) -> dict[str, str]:
    """{normalized: raw} from each product setup's descriptive header Insured.

    The header field is display-only — nothing matches on it — but it is where
    a broker typically lists every covered entity, so it is the best source of
    suggestions before any category names one.
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
        for name in insured_names(header.get("insured")):
            if norm := normalize_entity(name):
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
    roster_bucket: dict[str, dict] = {}
    for emp in employees:
        _tally(roster_bucket, (emp.attribute_values or {}).get("entity"))

    claimed = _category_entities(db, policy_year.id)
    configured = {**_setup_header_entities(db, policy_year.id), **claimed}

    roster = [
        EntityValue(
            value=slot["value"], count=slot["count"], claimed=norm in claimed
        )
        for norm, slot in sorted(
            roster_bucket.items(), key=lambda kv: (-kv[1]["count"], kv[0])
        )
    ]
    known = [
        EntityValue(value=raw, count=0, claimed=norm in claimed)
        for norm, raw in sorted(configured.items())
        if norm not in roster_bucket
    ]
    return EntityVocab(
        employees_total=len(employees), roster=roster, known=known
    )
