"""One-off audit: trace STM placement slip raw data -> parser output.

Verifies the SOB / plan / category / premium-rate mapping is correct.
"""
from __future__ import annotations

from pathlib import Path

from app.services.excel_reader import open_workbook
from app.services.placement_slip_parser import (
    NON_PRODUCT_SHEETS,
    parse_placement_slip,
)

STM = Path(r"C:\Users\huien\inspro\reference\STMicroelectronics - Placement Slips 2026_workingfile (1).xls")


def dump_raw_sheet(name: str, rows, max_rows=80, max_cols=16):
    print(f"\n{'='*100}\nRAW SHEET: {name}  ({len(rows)} rows)\n{'='*100}")
    for i, row in enumerate(rows[:max_rows]):
        cells = []
        for c in range(min(max_cols, len(row))):
            v = row[c]
            if v is None:
                continue
            s = str(v).replace("\n", "\\n")
            if len(s) > 22:
                s = s[:22] + "…"
            cells.append(f"[{c}]{s}")
        if cells:
            print(f"  r{i:>3}: " + " | ".join(cells))


def main():
    print(f"FILE: {STM.name}\nEXISTS: {STM.exists()}")
    if not STM.exists():
        return

    # 1. Raw dump of each product sheet
    with open_workbook(STM) as wb:
        print(f"\nSHEETS: {wb.sheet_names}")
        for name in wb.sheet_names:
            if name.strip().lower() in NON_PRODUCT_SHEETS:
                print(f"\n(skipping non-product sheet: {name})")
                continue
            sheet = wb.sheet(name)
            dump_raw_sheet(name, sheet.rows)

    # 2. Parser output
    parsed = parse_placement_slip(STM, client_label="STM-AUDIT")
    print(f"\n\n{'#'*100}\nPARSER OUTPUT\n{'#'*100}")
    print(f"client={parsed.client}  products={len(parsed.products)}")
    print(f"diagnostics={parsed.diagnostics}")

    for ps in parsed.products:
        print(f"\n{'-'*100}")
        print(f"PRODUCT: sheet={ps.sheet!r} code={ps.product_code!r}")
        print(f"  header: {ps.policy_header}")
        print(f"  categories={len(ps.categories)}  plans={len(ps.plans)}")
        print(f"\n  CATEGORIES (cat | plan_code | insured | basis | SI | rate | annual | rate_basis):")
        for c in ps.categories:
            print(
                f"    row{c.source_row:>3}: {c.category[:40]!r:<42} "
                f"plan={c.plan_code!r:<6} ins={c.insured[:10]!r:<12} "
                f"basis={str(c.basis)[:14]!r:<16} SI={c.sum_insured} "
                f"rate={c.premium_rate} ann={c.annual_premium} rb={c.rate_basis}"
            )
            if c.rate_tiers:
                print(f"           tiers={c.rate_tiers}")
        print(f"\n  PLANS (SOB):")
        for pl in ps.plans:
            print(f"    PLAN code={pl.code!r} name={pl.display_name!r} "
                  f"annual_limit={pl.annual_policy_limit!r} items={len(pl.items)}")
            print(f"      cover: {str(pl.cover_description)[:90]!r}")
            for it in pl.items[:25]:
                sub = f" sub={[(k,n,v) for k,n,v in it.sub_items]}" if it.sub_items else ""
                props = f" props={it.properties}" if it.properties else ""
                print(f"        {it.number}. {it.name[:38]!r:<40} = {it.value!r}{sub}{props}")

    # 3. Mapping check: category.plan_code -> a plan with matching code on same product?
    print(f"\n\n{'#'*100}\nMAPPING CHECK: category.plan_code -> Plan.code (per product)\n{'#'*100}")
    for ps in parsed.products:
        plan_codes = {pl.code for pl in ps.plans}
        cat_plan_codes = {c.plan_code for c in ps.categories if c.plan_code}
        print(f"\nPRODUCT {ps.product_code!r} (sheet {ps.sheet!r})")
        print(f"  plan codes from SOB    : {sorted(plan_codes)}")
        print(f"  plan codes on categories: {sorted(cat_plan_codes)}")
        unmatched = cat_plan_codes - plan_codes
        if plan_codes and unmatched:
            print(f"  !! categories referencing plan codes with NO SOB plan: {sorted(unmatched)}")
        elif not plan_codes and cat_plan_codes:
            print(f"  (no SOB plans on this product — categories carry plan codes {sorted(cat_plan_codes)})")
        elif plan_codes and not unmatched:
            print(f"  OK: every category plan_code maps to an SOB plan")


if __name__ == "__main__":
    main()
