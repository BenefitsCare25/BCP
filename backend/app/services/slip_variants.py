"""Decide which placement-slip sheets are one product and which are variants.

A workbook can carry several sheets of the same product type. Two different
situations look alike by sheet name:

* **One policy split across sheets** — VDL's "GHS - Locals / Secondees /
  Dependants": the same insurer, policyholder and insured, split by population.
  Those sheets MERGE into one product, as they always have.
* **Several policies of one type** — Vopak's "GHS (VTS) / GHS (VA) / GHS (BCSS)",
  one policy per legal entity (or one GHS per insurer). Each is its own
  contract with its own categories, rates and schedule, so each becomes a
  product VARIANT with its own code (``GHS-VTS``).

The deciding signal is the policy each sheet describes — its insurer,
policyholder and insured as printed in the sheet's own header — never the
sheet's wording. The label comes from the sheet's qualifier ("(VTS)") when it
has one, else from whichever header field tells the policies apart.

Pure and DB-free, like the parser: it rewrites ``ProductSlip.product_code`` and
the upload persists whatever codes it returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from app.services import product_registry
from app.services.slip_parsing.models import PlacementSlip, ProductSlip

# Header fields that identify a policy, in the order used to label variants
# whose sheet names carry no qualifier.
_IDENTITY_FIELDS = ("insured", "insurer", "policyholder")

# Separators inside an "Insured :" line that lists several legal entities:
# "1. A Pte Ltd 2. B Pte Ltd", "A Pte Ltd and/or B Pte Ltd", "A; B". Commas and
# "&" stay inside one name ("A Pte. Ltd., Singapore Branch", "Tan & Sons"); a
# list joined by them still overlaps each member by core-word containment.
_ENTITY_SPLIT = re.compile(r"(?:^|\s)\d+[.)]\s+|;|\n|\s+and/or\s+", re.IGNORECASE)

# Words that restate a company's legal form or domicile rather than name it:
# "AIA" and "AIA Singapore Private Limited" are one insurer.
_LEGAL_WORDS = frozenset({
    "pte", "ltd", "private", "limited", "plc", "inc", "llc", "co", "company",
    "corp", "corporation", "bhd", "berhad", "sdn", "singapore", "sg", "the",
})


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _core(value: str | None) -> frozenset[str]:
    """The words that name a party, legal-form words dropped."""
    return frozenset(w for w in _norm(value).split() if w not in _LEGAL_WORDS)


def _same_party(a: frozenset[str], b: frozenset[str]) -> bool:
    """One party however the header words it: a blank is no evidence, and one
    name's core words contained in the other's is the same party."""
    return not a or not b or a <= b or b <= a


def _entities(insured: str | None) -> frozenset[frozenset[str]]:
    return frozenset(
        core for part in _ENTITY_SPLIT.split(insured or "") if (core := _core(part))
    )


def _entities_overlap(
    a: frozenset[frozenset[str]] | set[frozenset[str]],
    b: frozenset[frozenset[str]] | set[frozenset[str]],
) -> bool:
    if not a or not b:
        return True
    return any(x <= y or y <= x for x in a for y in b)


@dataclass
class _Policy:
    """What the sheets of one policy state about it, merged as sheets join."""

    insurer: frozenset[str]
    policyholder: frozenset[str]
    entities: set[frozenset[str]]
    sheets: list[ProductSlip] = field(default_factory=list)

    def accepts(self, product: ProductSlip) -> bool:
        """Same policy unless the sheets DISAGREE: a different insurer or
        policyholder, or insured-entity lists with no entity in common.

        Names are compared by their core words, so "AIA" and "AIA Singapore Pte
        Ltd" agree. A field one sheet leaves blank is no evidence either way,
        and entity lists that merely differ are one policy split by population
        (VDL's Locals cover entity 1, its Secondees entities 1 and 2) — only
        lists sharing nothing describe separate contracts (Vopak: one per
        entity).
        """
        header = product.policy_header
        return (
            _same_party(self.insurer, _core(header.insurer))
            and _same_party(self.policyholder, _core(header.policyholder))
            and _entities_overlap(self.entities, _entities(header.insured))
        )

    def add(self, product: ProductSlip) -> None:
        header = product.policy_header
        self.insurer = self.insurer or _core(header.insurer)
        self.policyholder = self.policyholder or _core(header.policyholder)
        self.entities |= _entities(header.insured)
        self.sheets.append(product)


def _policies(members: list[ProductSlip]) -> list[list[ProductSlip]]:
    """Cluster sheets into policies, first-seen order."""
    policies: list[_Policy] = []
    for m in members:
        target = next((p for p in policies if p.accepts(m)), None)
        if target is None:
            target = _Policy(frozenset(), frozenset(), set())
            policies.append(target)
        target.add(m)
    return [p.sheets for p in policies]


def _identity(product: ProductSlip) -> tuple[str, ...]:
    header = product.policy_header
    return tuple(_norm(getattr(header, f, None)) for f in _IDENTITY_FIELDS)


def _base(product: ProductSlip) -> str:
    """The product type a sheet belongs to. An unrecognised code is its own
    base — the broker classifies it later — so it never groups with others."""
    code = product.product_code
    base = product_registry.base_code(code)
    return base if product_registry.get_entry(code) is not None else code


def _fallback_label(members: list[ProductSlip], product: ProductSlip) -> str:
    """The header field that differs across the group, for a sheet whose name
    carries no qualifier. Falls back to the sheet name."""
    for index, name in enumerate(_IDENTITY_FIELDS):
        if len({_identity(m)[index] for m in members}) > 1:
            value = getattr(product.policy_header, name, None)
            if value:
                return str(value)
    return product.sheet


def _existing_variant(
    base: str, sheets: list[ProductSlip], existing_codes: frozenset[str]
) -> tuple[str, str] | None:
    """``(code, label)`` of a variant the client already has that this policy's
    sheet names — so re-uploading one entity's slip on its own ("GHS (VTS)")
    lands on GHS-VTS again rather than on the base GHS product."""
    first = sheets[0]
    label = product_registry.sheet_qualifier(first.sheet, first.product_code).strip()
    if not label:
        return None
    code = product_registry.variant_code(base, label)
    return (code, label) if code != base and code in existing_codes else None


def assign_variants(
    slip: PlacementSlip, existing_codes: frozenset[str] = frozenset()
) -> PlacementSlip:
    """Return ``slip`` with every sheet's final product code.

    * One sheet of a type → the type's base code (``GHS (VTS)`` alone is GHS),
      unless the client already has that sheet's variant (``existing_codes``,
      upper-cased product codes), which it then keeps.
    * Several sheets describing ONE policy → all share the base code.
    * Several sheets describing DIFFERENT policies → one variant code per
      policy; sheets of the same policy share it.
    """
    groups: dict[str, list[ProductSlip]] = {}
    for product in slip.products:
        groups.setdefault(_base(product), []).append(product)

    final: dict[int, ProductSlip] = {}
    for base, members in groups.items():
        known = product_registry.get_entry(base) is not None
        policies = _policies(members)
        if len(policies) == 1:
            existing = _existing_variant(base, members, existing_codes) if known else None
            for m in members:
                final[id(m)] = (
                    replace(m, product_code=existing[0], variant_of=base,
                            variant_label=existing[1])
                    if existing is not None
                    else replace(m, product_code=base if known else members[0].product_code)
                )
            continue

        taken: set[str] = set()
        for sheets in policies:
            first = sheets[0]
            label = product_registry.sheet_qualifier(first.sheet, first.product_code)
            label = (label or _fallback_label(members, first)).strip()
            code = product_registry.variant_code(base, label)
            n = 2
            while code in taken or code == base:
                code = product_registry.variant_code(base, f"{label} {n}")
                n += 1
            taken.add(code)
            for m in sheets:
                final[id(m)] = replace(
                    m, product_code=code, variant_of=base, variant_label=label
                )

    return replace(slip, products=tuple(final[id(p)] for p in slip.products))
