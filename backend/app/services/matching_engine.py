"""Three-tier matching engine — brief §8.7.

Tiers: exact-name (dict lookup, score 1.0) → fuzzy Jaccard (threshold 0.6) →
JSONLogic rule eval. When several rules in the same product match one employee,
the MOST SPECIFIC rule wins (most leaf conditions), so a "Foreign Workers WP/SP
with grade 18+" rule beats a looser "grade 18+" rule for the same employee
instead of being shadowed by it. Ties are broken confirmed-first then priority
ASC, so a needs_review rule can never steal a match from an equally-specific
confirmed one. Per-employee evaluation is wrapped in try/except so one malformed
row can't abort the whole policy-year run.
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.core.deps import tenant_or_global
from app.models import Category, Employee, EmployeeAttributeSchema
from app.models.category import CategoryStatus
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.models.product import Product
from app.services.derivation_engine import derive
from app.services.rule_evaluator import evaluate

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.6
CONTAINMENT_CONFIDENCE = 0.7
COMMIT_BATCH_SIZE = 500
_TOKEN_RE = re.compile(r"[^a-z0-9]+")

# Tokens that carry no tier meaning — used to gate the containment fallback so a
# label made only of connectives can't be "contained" in everything.
_FUZZY_STOPWORDS = frozenset(
    {"and", "or", "to", "the", "of", "a", "an", "in", "for", "based", "with", "their"}
)

# Trailing qualifier clauses that bury the tier label in noise and wreck token
# overlap. Insurers append job-grade code maps and dependant-basis notes to the
# tier name, e.g.
#   "Executive to AM & Secretary (Job category: E1 to E4, EA to ED, ...)"
#   "SM and above (Job category: 99, A1 to A9, ...) / All Eligible Dependants ..."
# The leading words ARE the tier; the parenthetical/dependant tail is metadata.
# We strip it for fuzzy matching only — display_name/raw_description are untouched.
_DEPENDANTS_TAIL_RE = re.compile(
    r"\s*/.*?(?:dependants?|dependents?).*$", re.IGNORECASE
)
_PAREN_QUALIFIER_RE = re.compile(r"\([^)]*\)")

MatchMethod = Literal["exact_name", "fuzzy_name", "rule"]

# ── Insured-entity gate ───────────────────────────────────────────────────────
# Multi-subsidiary slips (WICA-style per-entity blocks) repeat category names
# per legal entity; the roster's "Entity" column says which company employs
# each member. A category whose plan_assignments.insured names specific
# entities only matches employees of those entities. Blank on EITHER side is a
# wildcard, so single-entity clients and rosters without an Entity column are
# untouched.

# Corporate-suffix canonicalization so punctuation/abbreviation variance can't
# split an entity ("CityNexus Pte. Ltd." == "CityNexus Pte Ltd";
# "… Limited" == "… Ltd").
_CORP_SUFFIX_MAP = {
    "private": "pte",
    "limited": "ltd",
    "incorporated": "inc",
    "corporation": "corp",
    "company": "co",
}


def normalize_entity(name: str | None) -> str:
    """Canonical form of a legal-entity name for comparison."""
    if not name:
        return ""
    tokens = [t for t in _TOKEN_RE.split(str(name).lower()) if t]
    return " ".join(_CORP_SUFFIX_MAP.get(t, t) for t in tokens)


def insured_entities(raw: object) -> frozenset[str]:
    """The set of normalized entity names in an Insured cell (comma-separated
    legal-entity list from the slip). Empty set = no entity restriction."""
    if not raw or not isinstance(raw, str):
        return frozenset()
    return frozenset(
        norm for part in raw.split(",") if (norm := normalize_entity(part))
    )


def category_insured_entities(cat: Category) -> frozenset[str]:
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    return insured_entities(pa.get("insured"))


def employee_entity(attribute_values: dict | None) -> str:
    """Normalized legal entity employing this member, from the roster's Entity
    column (attribute id "entity"). "" when the roster doesn't carry one."""
    if not attribute_values:
        return ""
    return normalize_entity(attribute_values.get("entity"))


def _entity_allows(cat_entities: frozenset[str], emp_entity: str) -> bool:
    return not cat_entities or not emp_entity or emp_entity in cat_entities


@dataclass(frozen=True)
class MatchOutcome:
    category_id: str | None
    method: MatchMethod | None
    confidence: float | None


@dataclass(frozen=True)
class MatchSummary:
    employees_total: int
    employees_matched: int
    by_method: dict[str, int]
    duration_ms: int
    errors: int = 0


def tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t for t in _TOKEN_RE.split(text.lower()) if t}


def canonicalize_category_name(name: str | None) -> str:
    """Strip trailing job-category code maps and dependant-basis notes from a
    category label so the human-readable tier name is what gets tokenized.

    ``"Manager (Job category: E5 to E6, EE to EF) / All Eligible Dependants"``
    → ``"Manager"``. Idempotent and a no-op for labels without qualifiers.
    """
    if not name:
        return ""
    s = _DEPENDANTS_TAIL_RE.sub("", name)
    s = _PAREN_QUALIFIER_RE.sub(" ", s)
    return s.strip()


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize(text: str) -> str:
    return text.strip().lower()


def _status_rank(status: str | None) -> int:
    """Lower is preferred: confirmed beats needs_review beats draft."""
    if status == CategoryStatus.confirmed.value:
        return 0
    if status == CategoryStatus.needs_review.value:
        return 1
    return 2


_LEAF_OPS = frozenset({"=", "!=", ">=", "<=", ">", "<", "between", "in", "not_in"})


def rule_specificity(rule: object) -> int:
    """Count leaf comparison nodes in a JSONLogic rule.

    Used to prefer the most specific matching rule within a product so a
    narrower category (e.g. ``grade>=18 AND pass in [WP,SP]``) wins over a
    broader one (``grade>=18``) instead of being shadowed by priority order.
    The empty ``{"and": []}`` catch-all scores 0, so any real condition wins.
    """
    if not isinstance(rule, dict):
        return 0
    total = 0
    for op, val in rule.items():
        if op in ("and", "or"):
            if isinstance(val, list):
                total += sum(rule_specificity(sub) for sub in val)
        elif op == "not":
            total += rule_specificity(val)
        elif op in _LEAF_OPS:
            total += 1
    return total


def match_one(
    employee: Employee,
    categories_by_priority: list[Category],
    exact_lookup: dict[str, Category],
    category_tokens: dict[str, set[str]],
    insured_by_category: dict[str, frozenset[str]] | None = None,
) -> MatchOutcome:
    """Match a single employee against pre-indexed category data.

    `categories_by_priority` drives the rule-evaluation order — already
    sorted by `(status_rank, priority)` so confirmed rules win ties.
    `exact_lookup` is `{normalized_display_name: category}` for O(1) tier 1.
    `category_tokens` is `{category_id: tokenized_display_name}` so tier 2
    doesn't re-tokenise the same category strings per employee.
    `insured_by_category` is `{category_id: normalized insured-entity set}`
    (computed from the categories when not supplied): a category restricted to
    specific legal entities never matches an employee of another entity, in ANY
    tier. An exact-name hit gated out falls through to fuzzy, where the sibling
    category of the employee's own entity (same name, different insured block)
    still matches at score 1.0.
    """
    if insured_by_category is None:
        insured_by_category = {
            c.id: category_insured_entities(c) for c in categories_by_priority
        }
    emp_entity = employee_entity(employee.attribute_values)

    def _allowed(cat: Category) -> bool:
        return _entity_allows(insured_by_category.get(cat.id, frozenset()), emp_entity)

    raw_category: str | None = (
        employee.attribute_values.get("category") if employee.attribute_values else None
    )

    # Tier 1 — exact name (dict lookup).
    if raw_category:
        hit = exact_lookup.get(_normalize(raw_category))
        if hit is not None and _allowed(hit):
            return MatchOutcome(hit.id, "exact_name", 1.0)

    # Canonicalize the employee label the SAME way category names were indexed
    # (strip "(Job category: …)" / dependant tails), so a parenthetical-laden
    # roster value is compared token-for-token against the cleaned tier name
    # rather than asymmetrically. Computed once and reused by tiers 2 and 4.
    emp_tokens = tokenize(canonicalize_category_name(raw_category)) if raw_category else set()

    # Tier 2 — fuzzy Jaccard on tokens. Tie-break: lower category.priority wins.
    if emp_tokens:
        best_score = 0.0
        best_priority: int | None = None
        best_cat: Category | None = None
        for cat in categories_by_priority:
            if not _allowed(cat):
                continue
            score = jaccard(emp_tokens, category_tokens.get(cat.id, set()))
            if score < FUZZY_THRESHOLD:
                continue
            if (
                best_cat is None
                or score > best_score
                or (
                    score == best_score
                    and best_priority is not None
                    and cat.priority < best_priority
                )
            ):
                best_score = score
                best_priority = cat.priority
                best_cat = cat
        if best_cat is not None:
            return MatchOutcome(best_cat.id, "fuzzy_name", round(best_score, 4))

    # Tier 3 — rule evaluation. Collect every matching rule in this product,
    # then pick the most specific (most leaf conditions); break ties
    # confirmed-first, then priority ASC. This stops a looser, earlier-priority
    # rule from shadowing a narrower one (e.g. foreign-worker plans).
    view = {
        **(employee.attribute_values or {}),
        **(employee.derived_attribute_values or {}),
    }
    matches = [
        cat
        for cat in categories_by_priority
        if _allowed(cat) and cat.matching_rule and evaluate(cat.matching_rule, view)
    ]
    if matches:
        best = min(
            matches,
            key=lambda c: (
                _status_rank(c.status),
                -rule_specificity(c.matching_rule),
                c.priority,
            ),
        )
        return MatchOutcome(best.id, "rule", best.confidence)

    # Tier 4 — containment fallback (LAST resort, after rules). A short roster
    # label ("Manager") is fully contained in a longer/grouped category label
    # ("Manager, Executive to AM and Secretary") that Jaccard misses. Runs only
    # when no exact/fuzzy/rule match fired, so a real (more specific) rule match
    # always wins over a loose name-subset. Accept when EVERY employee token is
    # in the category and at least one is meaningful (not a bare connective),
    # preferring the most specific candidate (fewest extra tokens), then lower
    # priority. Confidence is fixed below the fuzzy tier so reviewers can tell
    # containment matches apart.
    if emp_tokens and (emp_tokens - _FUZZY_STOPWORDS):
        best_extra: int | None = None
        best_priority = None
        best_cat = None
        for cat in categories_by_priority:
            if not _allowed(cat):
                continue
            cat_tokens = category_tokens.get(cat.id, set())
            if not emp_tokens <= cat_tokens:
                continue
            extra = len(cat_tokens - emp_tokens)
            if (
                best_cat is None
                or extra < best_extra
                or (extra == best_extra and cat.priority < best_priority)
            ):
                best_extra = extra
                best_priority = cat.priority
                best_cat = cat
        if best_cat is not None:
            return MatchOutcome(best_cat.id, "fuzzy_name", CONTAINMENT_CONFIDENCE)

    return MatchOutcome(None, None, None)


def _build_exact_lookup(categories: list[Category]) -> dict[str, Category]:
    """Build the {normalised display_name: category} index.

    When two categories share a display_name (rare but possible — e.g.
    duplicate AI suggestions), the one with the better (lower) status_rank
    wins, tie-broken by lower priority. Collisions are logged so admins
    can resolve them before activation.
    """
    out: dict[str, Category] = {}
    for c in categories:
        if not c.display_name:
            continue
        key = _normalize(c.display_name)
        existing = out.get(key)
        if existing is None:
            out[key] = c
            continue
        # Collision — prefer confirmed over needs_review, then lower priority.
        ex_rank = (_status_rank(existing.status), existing.priority)
        new_rank = (_status_rank(c.status), c.priority)
        winner = c if new_rank < ex_rank else existing
        logger.warning(
            "Category display-name collision %r — keeping %s (status=%s priority=%d), "
            "dropping %s (status=%s priority=%d)",
            c.display_name,
            winner.id,
            winner.status,
            winner.priority,
            (c if winner is existing else existing).id,
            (c if winner is existing else existing).status,
            (c if winner is existing else existing).priority,
        )
        out[key] = winner
    return out


@dataclass(frozen=True)
class _ProductIndex:
    product_id: str
    product_code: str
    categories_by_priority: list[Category]
    exact_lookup: dict[str, Category]
    category_tokens: dict[str, set[str]]
    insured_by_category: dict[str, frozenset[str]]


def _build_product_indices(
    categories: list[Category],
    product_lookup: dict[str, Product],
) -> list[_ProductIndex]:
    """Group categories by product and build match indices per product."""
    by_product: dict[str, list[Category]] = defaultdict(list)
    for c in categories:
        key = c.product_id or "__unlinked__"
        by_product[key].append(c)

    indices: list[_ProductIndex] = []
    for pid, cats in by_product.items():
        sorted_cats = sorted(cats, key=lambda c: (_status_rank(c.status), c.priority))
        product = product_lookup.get(pid)
        indices.append(_ProductIndex(
            product_id=pid,
            product_code=product.code if product else "?",
            categories_by_priority=sorted_cats,
            exact_lookup=_build_exact_lookup(sorted_cats),
            category_tokens={
                c.id: tokenize(canonicalize_category_name(c.display_name))
                for c in sorted_cats
            },
            insured_by_category={
                c.id: category_insured_entities(c) for c in sorted_cats
            },
        ))
    return indices


def match_policy_year(
    db: Session,
    policy_year_id: str,
    user: CurrentUser,
) -> MatchSummary:
    """Re-derive every employee's attributes, then match against each product's
    categories independently. Stores per-product results in
    ``Employee.matched_categories`` and keeps ``matched_category_id`` pointing
    to the highest-confidence match for backward compatibility.

    NEVER commits — the caller owns the transaction (audit entry + commit), so
    a failure mid-run rolls back the WHOLE run. (Mid-loop commits used to leave
    half the roster on new matches and half stale when a run died partway.)
    Large rosters are flushed in batches to bound the dirty set.
    """
    started = time.monotonic()

    categories = list(
        db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        )
        .scalars()
        .all()
    )
    category_ids = {category.id for category in categories}

    product_ids = {c.product_id for c in categories if c.product_id}
    products = (
        list(db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all())
        if product_ids
        else []
    )
    product_lookup: dict[str, Product] = {p.id: p for p in products}

    product_indices = _build_product_indices(categories, product_lookup)

    schemas = list(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, user.client_id)
            )
        )
        .scalars()
        .all()
    )

    # Terminated employees keep their historical match but are never
    # re-evaluated (they carry no live coverage).
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        )
        .scalars()
        .all()
    )

    by_method: dict[str, int] = {
        "exact_name": 0, "fuzzy_name": 0, "rule": 0, "manual_override": 0
    }
    matched = 0
    errors = 0
    pending_in_batch = 0

    for emp in employees:
        try:
            emp.derived_attribute_values = derive(emp.attribute_values or {}, schemas)

            # Operator-pinned matches survive a re-run, but references to
            # categories that were deleted must be removed.
            if emp.match_method == "manual_override":
                valid_overrides = [
                    entry
                    for entry in (emp.matched_categories or [])
                    if isinstance(entry, dict)
                    and entry.get("category_id") in category_ids
                ]
                emp.matched_categories = valid_overrides or None
                emp.matched_category_id = (
                    str(valid_overrides[0]["category_id"])
                    if valid_overrides
                    else None
                )
                emp.match_method = "manual_override" if valid_overrides else None
                emp.match_confidence = 1.0 if valid_overrides else None
                if valid_overrides:
                    matched += 1
                    by_method["manual_override"] += 1
                pending_in_batch += 1
                if pending_in_batch >= COMMIT_BATCH_SIZE:
                    db.flush()
                    pending_in_batch = 0
                continue

            all_matches: list[dict[str, object]] = []
            best_outcome = MatchOutcome(None, None, None)
            best_confidence: float = -1.0

            for idx in product_indices:
                outcome = match_one(
                    emp,
                    idx.categories_by_priority,
                    idx.exact_lookup,
                    idx.category_tokens,
                    idx.insured_by_category,
                )
                if outcome.category_id is None:
                    continue
                all_matches.append({
                    "category_id": outcome.category_id,
                    "product_code": idx.product_code,
                    "method": outcome.method,
                    "confidence": outcome.confidence,
                })
                conf = outcome.confidence if outcome.confidence is not None else 0.0
                if conf > best_confidence:
                    best_confidence = conf
                    best_outcome = outcome

        except Exception:
            logger.exception(
                "Match failure for employee %s (staff_id=%s); marking unmatched",
                emp.id, emp.staff_id,
            )
            emp.matched_category_id = None
            emp.match_method = None
            emp.match_confidence = None
            emp.matched_categories = None
            errors += 1
            pending_in_batch += 1
            continue

        emp.matched_category_id = best_outcome.category_id
        emp.match_method = best_outcome.method
        emp.match_confidence = best_outcome.confidence
        emp.matched_categories = all_matches if all_matches else None

        if all_matches:
            matched += 1
            if isinstance(best_outcome.method, str) and best_outcome.method in by_method:
                by_method[best_outcome.method] += 1

        pending_in_batch += 1
        if pending_in_batch >= COMMIT_BATCH_SIZE:
            db.flush()
            pending_in_batch = 0

    duration_ms = int((time.monotonic() - started) * 1000)
    return MatchSummary(
        employees_total=len(employees),
        employees_matched=matched,
        by_method=by_method,
        duration_ms=duration_ms,
        errors=errors,
    )


__all__ = [
    "FUZZY_THRESHOLD",
    "MatchMethod",
    "MatchOutcome",
    "MatchSummary",
    "employee_entity",
    "insured_entities",
    "jaccard",
    "match_one",
    "match_policy_year",
    "normalize_entity",
    "tokenize",
]
