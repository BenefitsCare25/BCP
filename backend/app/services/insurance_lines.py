"""Product *insurance lines* — the broker-facing Medical / Life / Flex grouping.

This is a **separate dimension** from ``form_profiles`` (which decides *form
structure*). A product's structural profile and its insurance line are
orthogonal: e.g. Group Personal Accident (``GPA``) keeps ``form_profile =
"accident"`` but belongs to the **Life** line because brokers configure it
alongside term life and critical illness.

The line is inferred from the product code and can be overridden per product via
``product_metadata['line']`` (the same writable channel ``form_profiles`` uses
for ``form_profile``) — so no schema migration is needed and custom products
created under a given tab carry that tab's line.

Like ``form_profiles``, this module holds *classification only* — no scheme
values — and imports nothing from ``product_templates`` to keep the dependency
one-directional.
"""
from __future__ import annotations

from typing import Literal, cast

from app.services import product_registry

InsuranceLine = Literal["medical", "life", "flex"]

# Unknown codes (and the unassigned category group) default to Medical — the
# most common line. Revisit if life-shaped unknown codes start appearing.
DEFAULT_LINE: InsuranceLine = "medical"

# ── code → line (catalog codes; compound slip codes resolve to these via the
#    product match, so the catalog code is what we see here). Derived from the
#    product registry — add new products there, not here. Note GPA keeps
#    form_profile "accident" but is configured on the Life tab. ───────────────
_CODE_LINE: dict[str, InsuranceLine] = cast(
    "dict[str, InsuranceLine]", product_registry.code_line_map()
)


def infer_line(code: str, override: str | None = None) -> InsuranceLine:
    """Resolve a product's insurance line: a *valid* explicit override wins,
    else inferred from the code, else the default (Medical). An unrecognized
    override is ignored (falls through to inference) rather than mis-bucketing.
    """
    if override in ("medical", "life", "flex"):
        return override  # type: ignore[return-value]
    return _CODE_LINE.get((code or "").strip().upper(), DEFAULT_LINE)
