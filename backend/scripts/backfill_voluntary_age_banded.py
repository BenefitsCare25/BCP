"""Backfill voluntary life categories to the age-banded rate model.

The parser used to copy the COMPULSORY flat ``premium_rate`` and a carried-forward
group ``sum_insured`` / headcount onto VOLUNTARY (upgrade/downgrade) tiers of life
products. Those figures are meaningless for an elective tier — its premium is
age-banded (``basis / 1000 x rate[member's age band]``) — and they made the config
"Rate" screen show a wrong flat-rate "total premium" for voluntary plans.

``_build_plan_assignments`` now drops them at parse time and flags the assignment
``rate_basis = "age_banded"``. This backfill applies the same cleanup to
already-parsed categories so existing policy years are corrected without a
re-upload: for a voluntary category that carries a ``voluntary_rates`` table it
removes ``premium_rate`` / ``sum_insured`` / ``annual_premium`` / ``num_employees``
/ ``rate_tiers`` and sets ``rate_basis = "age_banded"`` (keeping ``basis`` +
``voluntary_rates``).

Multi-tenant: on Postgres each firm's ``categories`` live in a per-firm
``firm_<id>`` schema, so this iterates every firm and sets the search_path per
firm (mirrors ``regenerate_category_rules.py``). On SQLite it runs once.

Safety:
- Only categories whose ``participation_model == 'voluntary'`` AND whose
  ``plan_assignments`` already carries a ``voluntary_rates`` table are touched —
  a voluntary tier with no parsed age-band table is left alone (it would need a
  re-upload to gain one) and compulsory tiers are never touched.
- Dry-run by default: prints what WOULD change. Pass ``--apply`` to commit
  (committed per firm so a failure in one firm doesn't roll back the rest).

Usage:
    uv run python -m scripts.backfill_voluntary_age_banded            # dry-run
    uv run python -m scripts.backfill_voluntary_age_banded --policy-year <id>
    uv run python -m scripts.backfill_voluntary_age_banded --apply
"""
from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.db.tenancy import is_postgres, set_search_path
from app.models import BrokerFirm
from app.models.category import Category
from app.services.plan_hydration import GROUP_RATE_FIELDS

# Group/flat-rate keys that are meaningless on an age-banded voluntary tier —
# shared with the parser persistence so the two can't drift.
_STALE_KEYS = GROUP_RATE_FIELDS


def _backfill_session(
    db: Session, policy_year_id: str | None, apply: bool
) -> tuple[int, int]:
    """Clean voluntary age-banded categories in the session's current schema.

    Returns ``(changed, examined)``. Does not commit — the caller owns the
    transaction so it can scope commits per firm.
    """
    changed = examined = 0
    stmt = select(Category).where(Category.participation_model == "voluntary")
    if policy_year_id:
        stmt = stmt.where(Category.policy_year_id == policy_year_id)

    for cat in db.execute(stmt).scalars():
        pa = dict(cat.plan_assignments or {})
        if not pa.get("voluntary_rates"):
            continue  # no age-band table parsed → can't age-band; leave as-is
        examined += 1
        new_pa = {k: v for k, v in pa.items() if k not in _STALE_KEYS}
        new_pa["rate_basis"] = "age_banded"
        if new_pa == pa:
            continue
        changed += 1
        dropped = [k for k in _STALE_KEYS if k in pa]
        print(
            f"[{'APPLY' if apply else 'DRY '}] {cat.id} "
            f"{(cat.display_name or '')[:48]!r} — drop {dropped}, rate_basis=age_banded"
        )
        if apply:
            # Reassign (don't mutate in place) so SQLAlchemy's JSON change
            # tracking flags the column dirty.
            cat.plan_assignments = new_pa
    return changed, examined


def backfill(policy_year_id: str | None, apply: bool) -> int:
    """Run across every firm schema (Postgres) or the single schema (SQLite).
    Returns the total number of categories changed."""
    changed = examined = 0

    if is_postgres(engine):
        with SessionLocal() as db:
            firm_ids = list(db.execute(select(BrokerFirm.id)).scalars().all())
        for fid in firm_ids:
            with SessionLocal() as db:
                set_search_path(db, fid)
                c, e = _backfill_session(db, policy_year_id, apply)
                if apply:
                    db.commit()
            changed += c
            examined += e
        scope = f"{len(firm_ids)} firm schema(s)"
    else:
        with SessionLocal() as db:
            changed, examined = _backfill_session(db, policy_year_id, apply)
            if apply:
                db.commit()
        scope = "single schema (SQLite)"

    verb = "updated" if apply else "would update"
    print(
        f"\n{verb} {changed} of {examined} voluntary age-banded categories "
        f"across {scope}."
    )
    if not apply and changed:
        print("Re-run with --apply to persist.")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-year", dest="policy_year", default=None)
    parser.add_argument("--apply", action="store_true", help="commit changes")
    args = parser.parse_args()
    backfill(args.policy_year, args.apply)


if __name__ == "__main__":
    main()
