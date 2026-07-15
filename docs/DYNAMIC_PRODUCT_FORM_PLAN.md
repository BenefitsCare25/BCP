# Dynamic Product Setup Form — Implementation Plan

Status: **IMPLEMENTED** (2026-06-02) · Author: Claude

> Shipped: profile-driven basis/rate/benefit schemas, parser extraction across
> all slip archetypes (incl. per-member rate tables), 10 canonical templates
> (GMM, SP, GCGP, GCSP, GP, GD, GTL, GDD, GCI, GPA), and GP/SP/GCI/GD/OSI
> classification fixes. 255 backend tests pass; `ruff` clean; frontend builds.
> See §10 for the as-built notes.

Goal: make the guided product-setup form (`ProductSetupForm`) correctly model
**every** medical and life product seen in the reference placement slips, not
just GHS. Today only `ghs.v1.json` is a real template; all other products fall
back to a blank, medical-shaped skeleton with the wrong basis-of-cover, rate,
and benefit structures.

---

## 1. Evidence — product structures in `reference/`

Parsed from the 6 client slips (Hartree, PNG, CBRE Group, CBRE MCST, "Placement
Slips 2026", STM, VDL). Distinct structural archetypes:

| Code(s) | Product | Basis-of-cover shape | Rate shape | Schedule-of-Benefits shape |
|---|---|---|---|---|
| GHS, GMM, GHS2, GMM2 | Hospital/Major Medical | **Tiered** EO/ES/EC/EF; GMM adds Core/Upgrade/Downgrade blocks | per-tier rate+premium | amount/text/days lines + 1 sub-level |
| SP, GCGP, GCSP, GP, GOSP, GOGP | Clinical GP / Specialist (outpatient) | **Per-member** (single count, untiered) | "Member Rate", split **Employee vs Dependent** | A–G **boolean** feature flags + Panel/Polyclinic/Non-Panel × (per-visit / co-pay / per-year) **matrix** |
| GD, DENTAL | Dental | Per-member / per-category | per-plan | **Panel vs Non-Panel column axis** + per-procedure caps |
| OSI | Secondment | Home-country / Host-country columns | per-tier | travel-style benefit list |
| GTL, GDD, GDI, GTPD | Term Life / Dread Disease / TPD | **Sum-assured**: `Basis` (flat / "12× monthly salary" / "% of GTL") + `Sum Insured`, multi-block participation | **Rate per S$1,000 SI** → premium = SI/1000 × rate | GDD/GTPD: **list** of covered conditions |
| GCI | Critical Illness (additional from GTL) | Sum-assured (mirrors GTL) | Rate per S$1,000 SI | **list** of conditions |
| GPA | Personal Accident | Sum-assured + Max-limit-per-person | Rate per S$1,000 SI | **scale of compensation** (event → %) + benefit list |

Header/meta fields present in slips but not guaranteed by the form: Eligibility
Date, dual address (ACRA + Mailing), **Product Rated Together**, **Policyholder(s)
Rated Together**, **Experience Refund Formula / Max Loss Ratio**, **Annual
Premium (subj. GST)**, **Non-Evidence Limit** (life), **Maximum Limit Per
Insured Person** (life/accident).

## 2. Current model (what we're extending)

- `app/services/form_profiles.py` — `_CODE_PROFILE` (code→profile), `PROFILE_SECTIONS`
  (all 5 profiles share the same 5 sections today), `PROFILE_FIELD_SPECS`.
- `app/services/product_templates.py` — `ProductTemplate` pydantic model; one
  template file `app/templates/ghs.v1.json`.
- Frontend `types.ts` — `BasisOfCoverRow` (insured/category/participation/
  plan_code/tiers/num_employees), `RateCell`, `BenefitItemAnswer` (kind =
  `amount|text|days`).
- Sections: `BasisOfCoverSection.tsx`, `RateTableSection.tsx`,
  `ScheduleOfBenefitsSection.tsx`.
- Confirm/materialization: `api/v1/product_setups.py::_category_plan_assignments`
  already discriminates `rate_basis ∈ {flat, tiered}` — **the extension point**
  for `per_member` and `per_1000_si`.

## 3. Bugs to fix first (cheap, high-value)

> **Two orthogonal dimensions.** `insurance_lines.py` decides the **tab**
> (Medical / Life / Flex) a product appears under; `form_profiles.py` decides the
> **form structure**. They are intentionally independent (e.g. GPA is on the Life
> tab but uses an `accident` form). The fixes below target *form structure* except
> the GP row, which is wrong on **both** axes.

In `form_profiles._CODE_PROFILE` (form structure):

| Code | Today | Correct | Why |
|---|---|---|---|
| `GP` | `sum_assured` (life) | `outpatient` (per-member medical) | Slips: GP = "Group Clinical General Practitioner Insurance". seed_demo also mislabels it "Group Personal". |
| `SP` | `tiered_medical` | `outpatient` (per-member) | Real SP is per-member "Member Rate", not EO/ES/EC/EF. |
| `GCI` | `accident` | `sum_assured` | GCI mirrors GTL exactly; accident fields don't apply. |
| `GD`/`DENTAL` | `tiered_medical` | `dental` (Panel/Non-Panel axis) | Panel/Non-Panel columns, not tiered. |
| `OSI` | `tiered_medical` | revisit (secondment/travel) | Home/Host-country + travel benefits. |

In `insurance_lines._CODE_LINE` **and** the client mirror `frontend/src/lib/insuranceLines.ts`
(tab placement) — **decided 2026-06-02**:

| Code | Today | Correct | Why |
|---|---|---|---|
| `GP` | `life` | `medical` | GP is an outpatient GP medical product; it was the one genuine tab misclassification. Move both the backend map and the client mirror together. |

These are independent of the schema work and can ship as a standalone PR with a
test in `tests/test_form_profiles.py` + `tests/test_insurance_lines.py` (new)
asserting each code→profile and code→line.

## 4. Target architecture — profile = full form schema

Promote `form_profile` from "section list + a few fields" to three pluggable
sub-schemas. One renderer, profile-driven layout. **GHS stays byte-identical**
(it's the `tiered`/`tiered`/`amount` preset).

### 4.1 `basis_model` — Basis-of-Cover column schema

Add `basis_model: Literal["tiered","per_member","sum_assured"]` to
`ProductTemplate` / profile. Generalize `BasisOfCoverRow` with an additive,
backward-compatible field bag:

```ts
export interface BasisOfCoverRow {
  id: string;
  insured: string;
  category: string;
  participation: string;     // free text; presets seed common values per block
  plan_code: string;
  tiers: Record<string, number>;   // tiered only (today)
  num_employees?: number;          // per_member / sum_assured
  // NEW (sum_assured):
  sum_insured?: number | null;
  basis?: string | null;           // "flat" | "12x monthly salary" | "% of GTL" (free text + presets)
}
```

`BasisOfCoverSection` renders columns from `basis_model`:
- `tiered` → existing EO/ES/EC/EF headcount columns.
- `per_member` → single "No. of members" count.
- `sum_assured` → `Basis` (combobox w/ suggestions) + `Sum Insured` (money) +
  `No. of employees`.

### 4.2 `rate_model` — rate table schema

Add `rate_model: Literal["tiered","per_member","per_1000_si"]`. `RateTableSection`
switches layout; materialization extends `_category_plan_assignments`:

| rate_model | UI columns | premium derivation | `rate_basis` persisted |
|---|---|---|---|
| `tiered` | rate+premium per tier | sum of tier premiums (today) | `tiered` |
| `per_member` | member rate (+ optional dependant rate), count | rate × count | `per_member` (new) |
| `per_1000_si` | rate per 1,000 SI | **auto** = ΣSI/1000 × rate | `per_1000_si` (new) |

`per_1000_si` premium is computed from the Basis-of-Cover `sum_insured` totals,
so the Rate section shows a read-only computed Annual Premium.

### 4.3 `benefit_schema` — Schedule-of-Benefits item kinds

Extend `BenefitKind`:
```
amount | text | days            (today)
boolean    → yes/no feature flag (GCGP A–G)
copay      → {per_visit, co_payment, per_year} triple (outpatient panel rows)
list       → enumerated covered-conditions list (GDD/GCI)
scale      → event → % of capital sum table (GPA)
```
Plus an optional **second column axis** on a benefit group (`column_axis:
["Panel","Non-Panel"]` for dental; `["Panel","Polyclinic","Non-Panel"]` for
outpatient) so the matrix renders without abusing sub-items.

`ScheduleOfBenefitsSection` gains a small per-kind renderer registry; unknown
kinds degrade to the current text input (forward-compatible).

## 5. Backward compatibility & migration

- `product_setups` stores `answers` as JSON (no column migration). New fields are
  additive and optional; `normalizePlans`/`buildAnswers` already backfill missing
  keys, so **resumed drafts and confirmed setups keep working**.
- `_category_plan_assignments` keeps `flat`/`tiered`; adds `per_member`/`per_1000_si`
  branches. Existing `Category.plan_assignments` rows are untouched.
- Template files are versioned (`<code>.v<n>.json`); GHS stays `v1`.
- No `sa.Enum` columns added (per CLAUDE.md) — all new discriminators are JSON
  strings / Python `Literal`.

## 6. Canonical templates to hand-author (`app/templates/`)

One curated `*.v1.json` per product, modeled on `ghs.v1.json`. Grouped by build
priority (medical + life only, per request):

**Medical**
- `gmm.v1.json` — tiered, multi-block participation, top-up benefit lines.
- `sp.v1.json` — per_member, specialist referral limit lines.
- `gcgp.v1.json` / `gogp.v1.json` — outpatient, A–G boolean flags + panel/
  polyclinic/non-panel copay matrix.
- `gcsp.v1.json` / `gosp.v1.json` — outpatient specialist + diagnostic.
- `gp.v1.json` — outpatient (alias-shaped to gcgp).
- `gd.v1.json` (`dental.v1.json` alias) — dental, Panel/Non-Panel axis, procedure caps.

**Life**
- `gtl.v1.json` — sum_assured, per_1000_si, Basis presets.
- `gdd.v1.json` — sum_assured + `list` of dread diseases.
- `gci.v1.json` — sum_assured + `list` of conditions.
- `gpa.v1.json` — sum_assured + `scale` of compensation + benefit list.

(GHS2/GMM2/IMP/MATERNITY/VISION/WELLNESS/GDI/GTPD/OSI/WICA: out of scope for this
phase; they keep falling back to the nearest profile preset until prioritized.)

## 7. Build order (phased PRs)

1. **PR-1 — classification fix.** `_CODE_PROFILE` + `PRODUCT_CATALOG` corrections;
   add `outpatient` + `dental` profiles (initially same sections as medical,
   distinct `basis_model`/`rate_model`); `tests/test_form_profiles.py`.
2. **PR-2 — backend schema.** Add `basis_model`/`rate_model`/extended `BenefitKind`
   + `column_axis` to `ProductTemplate` & `form_profiles`; extend
   `_category_plan_assignments` (`per_member`, `per_1000_si`); unit tests for
   materialization of each rate_basis.
3. **PR-3 — frontend schema.** Extend `types.ts`; make `BasisOfCoverSection` /
   `RateTableSection` profile-driven; add per-kind SOB renderers. `pnpm build` green.
4. **PR-4 — templates.** Hand-author the medical set (GMM, SP, GCGP/GCSP, GP, GD).
5. **PR-5 — templates.** Hand-author the life set (GTL, GDD, GCI, GPA).
6. **PR-6 — header/meta fields.** Add Eligibility Date, rated-together, experience
   refund, NEL / max-limit fields to the standard header/profile sets.

Each PR independently shippable; PR-1 can land immediately.

## 8. Verification (per CLAUDE.md)

- `cd backend && PYTHONPATH=. uv run pytest` (152 tests + new).
- `cd backend && uv run ruff check app tests scripts`.
- `cd frontend && pnpm build` (tsc -b + vite).
- New: `tests/test_form_profiles.py` (code→profile), materialization tests for
  `per_member`/`per_1000_si`, and — since no new tenant endpoints are added —
  no new `tests/test_tenant_isolation.py` rows required (confirm path already
  goes through `load_policy_year`).
- Manual: set up one product per archetype in the demo client; confirm; verify
  the matched-plan financials view reads `plan_assignments` unchanged.

## 9. Resolved decisions (2026-06-02)

- **GP classification → Medical + outpatient form.** Fix both `_CODE_LINE`
  (life→medical) and `_CODE_PROFILE` (sum_assured→outpatient).
- **Sum-assured `Basis` → free-text + presets.** Broker picks/types the basis
  (flat amount / "12× monthly salary" / "% of GTL") and enters the resulting
  `Sum Insured` manually. No salary-data wiring this phase — matches how slips
  present pre-computed illustration figures. (`per_1000_si` premium is computed
  from the entered SI totals.)
- **Multi-block participation → flat rows + presets.** Each block (Core /
  Voluntary-Upgrade / Downgrade / Dependent) is an ordinary Basis-of-Cover row
  distinguished by the free-text `participation` column, seeded with per-profile
  preset values. No nested "block" structure.
- **Dental Panel/Non-Panel → benefit `column_axis`.** Panel/Non-Panel render as
  columns within each benefit line (per-procedure caps differ by column), not as
  separate plans/enrolment.

### Remaining low-risk notes
- **`per_1000_si` computed premium** assumes one rate per plan/category; verify
  no slip uses banded SI rates (none seen so far).
- Hand-authoring 9 templates is the bulk of the effort; structures above are
  transcribed from real slips so content is grounded.

## 10. As-built notes (2026-06-02)

**Backend**
- `form_profiles.py`: added `outpatient` + `dental` profiles; `BasisModel`/`RateModel`
  literals + `PROFILE_BASIS_MODEL`/`PROFILE_RATE_MODEL` maps + `basis_model_for`/
  `rate_model_for`; corrected `_CODE_PROFILE` (GP/SP/GCGP/GCSP/GOSP/GOGP/OSI ->
  outpatient, GCI -> sum_assured, GD/DENTAL -> dental).
- `product_templates.py`: `ProductTemplate` gains `basis_model`, `rate_model`,
  `column_axis`; `BenefitKind` extended with `boolean|copay|list|scale`; validator
  fills models from profile (explicit values respected).
- `insurance_lines.py` + `frontend/src/lib/insuranceLines.ts`: GP -> medical.
- `slip_to_setup.py`: rate table now emits a `flat` cell for per-member /
  per-1000 rates; category rows carry `sum_insured`/`basis`/`num_employees`;
  benefit items carry `kind`.
- `product_setups.py`: `_category_plan_assignments` handles `per_member` and
  `per_1000_si` (auto-computes premium) and persists `sum_insured`/`basis`;
  threaded from `tpl.rate_model`/`basis_model`.
- `placement_slip_parser.py`: `_parse_flat_rates` recognises the per-member rate
  layout (Plan-column key + "Per Insured / Member Rate"); `_enrich_with_rates`
  expands compound plan keys ("1A/1B", "1 - Employees"); split-header fallback
  scans two rows below "Rate :".
- `seed_demo.py`: GP relabelled "Group Clinical General Practitioner" (outpatient).

**Frontend**
- `types.ts`: `BasisModel`/`RateModel`, extended `BenefitKind`, `ProductTemplate`
  gains `basis_model`/`rate_model`/`column_axis`, `BasisOfCoverRow` gains
  `sum_insured`/`basis`, `BenefitItemAnswer` gains `kind`.
- `BasisOfCoverSection` renders per `basis_model` (tiered tiers / per-member count
  / sum-assured basis+SI+headcount).
- `RateTableSection` renders per `rate_model`; per-member & per-1000-SI show a
  live computed annual premium from the Basis-of-Cover driver totals.
- `ScheduleOfBenefitsSection` adds renderers for boolean (Yes/No), copay (hint),
  list (conditions), scale (event rows) and the dental Panel/Non-Panel axis.

**Templates** (`backend/app/templates/`): gmm, sp, gcgp, gcsp, gp, gd, gtl, gdd,
gci, gpa (each `*.v1.json`). GHS unchanged.

**Tests**: extended `test_form_profiles.py` (classifications + basis/rate models +
validator), `test_insurance_lines.py` (GP medical); new `test_setup_materialization.py`
(3 rate models round-trip) and `test_parser_rate_models.py` (per-member +
per-1000 + tiered extraction, file-free synthetic rows).

**Known tail gaps (acceptable, documented)**
- Single-category life schemes with no plan code (e.g. CBRE GTL "All employees")
  prefill SI + basis but not the rate table (no plan key to map to); the broker
  assigns a plan + rate on review.
- Multi-block voluntary (GMM Upgrade/Downgrade, GTL Compulsory/Voluntary) and a
  few single-category tiered rate rows still need manual premium entry.

## 11. Schedule-of-Benefits audit (2026-06-02)

Audited every product's SOB end-to-end (parse -> synthesize -> merge_file_overlay
-> build_setup_answers) against all reference slips. Findings + fixes:

- The parser extracts **numbered-line medical SOBs** richly (GHS 102 lines/6 plans,
  GMM 36, SP 18, GCSP 6, GHS variants 85-136). For these the slip structure wins
  and per-client **values prefill** (GHS: 74 values, GMM: 13, SP/GCSP: 3-4).
- It **under-extracts** the outpatient A-G + panel/polyclinic/non-panel matrix
  (GCGP/GP), dental Panel/Non-Panel columns (GD), and condition/scale lists
  (GCI/GDD/GPA). `merge_file_overlay` now prefers the slip's lines only when they
  are >= the curated template's count and the template isn't a column-axis
  layout; otherwise the **curated template structure wins** (proper boolean/copay/
  list/scale kinds, Panel/Non-Panel axis) so the form is never a broken partial.
- Added a **template alias map** (`product_templates._TEMPLATE_ALIASES`):
  DENTAL->GD, GOGP->GCGP, GOSP->GCSP, GHS2->GHS, GMM2->GMM — reuse a sibling's
  curated template (keeping the requested code) instead of cloning JSON.
- Authored **osi.v1.json** (secondment: tiered + travel/PA amount + scale lines);
  reclassified OSI to tiered_medical (its slip uses EO/ES/EC/EF tiers).

**Result:** every medical + life product renders a complete, correct SOB.

**Documented boundary:** for template-sourced products (GCGP/GP/GD/GPA/GCI/GDD/
OSI) the STRUCTURE is curated-complete but per-client VALUES are not auto-prefilled
from the slip — the parser can't reliably extract those non-numbered layouts, so
the broker enters values with the slip as reference. GBT (travel) and WICA/WICI
(statutory) are premium-only with no SOB and are out of the medical/life scope.
Extending value extraction for the outpatient panel matrix / dental columns / GPA
scale is a scoped follow-up (dedicated parser work, regression-tested).
