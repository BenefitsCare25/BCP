# Full Employee Listing

Reports Center → Policy Admin → Internal registers → **Full Employee Listing (EL)**.

The workbook reproduces the company's own Employee Listing (`services/full_el/`). It
replaced the earlier 7-sheet workbook (Basis of Cover / Full EL / Dependants / Source
Setup / …), which did not match how clients keep their listing. Tenant authorization,
NRIC masking, All / Active only and the export audit are unchanged.

## Layout

- **Company listing uploaded.** A company that uploaded its own listing
  ([EMPLOYEE_LISTING_IMPORT.md](EMPLOYEE_LISTING_IMPORT.md)) gets that layout back:
  the same blocks, headings, column positions and listed category wording.
- **No listing uploaded.** Other companies get a layout in the same template shape,
  built from their products. Blocks are grouped by product type, so a directors'
  policy variant shares its type's block. Each block's columns follow how the product
  is rated:
  - **Life** (GTL): admin, age, category, present / eligible / pending SI,
    underwriting status, decision, letter dates, last accepted SI, loading,
    acceptance date, premium.
  - **Per-$1,000 SI** (GPA): admin, age, category, eligible SI, premium, premium
    with GST.
  - **Family-tier medical** (GHS, SP): admin, age, category, plan, family group,
    premium, premium with GST.
  - **Flat / per-member** (GP, dental): admin, age, category, plan, premium,
    premium with GST.
  - **Payroll- or policy-rated products** (WICA, annual business travel) get no
    per-person block.

Because the download matches the upload layout, it doubles as the editable listing.
Exporting GAS and re-uploading the file previews 0 changes for 1,939 people, with all
19 category mappings restored. Masked identifiers in a masked download are never
imported over real ones.

## Sheets

| Sheet | Content |
| --- | --- |
| Summary - Basis of Cover | Product, insurer, policy number, period, administration and categories per block |
| Employee Listing | Row 1 reference date (and one per-$1,000 rate when a block has a single rate); row 2 banners; row 3 headings; each employee followed by their dependants |
| Declaration - Please Read | Company declaration naming the broker firm |
| Inspro Use - System Category | Grade and work pass against each block's category, as applied |
| Headcount Summary | Live COUNTIFS / SUMIFS of employees, dependants, SI, premium and GST by category |
| Setup & Data Gaps | Unconfirmed setup, missing rates, unknown GST, members without cover, salary or DOB, members above the NEL |

## Row values

- **Category and plan.** The person's matched category per block, written in the
  listing's own wording when known. The plan is the listed wording, otherwise the
  plan code. A dependant row carries the cover listed for that dependant. Without a
  listing, dependants are covered under categories that cover dependants.
- **Family group.** Derived from the covered dependants (EO / ES / EC / EF).
- **Age.** A live formula against the row-1 reference date (the benefit-year start).
  It uses ANB when the slip's eligibility says ANB; the heading reads "Age (ANB)".
- **Eligible SI.** A salary multiple (`=MIN(48*N4,1600000)`) or a flat amount,
  capped at the slip's maximum per insured person.
- **Pending SI.** `=MAX(eligible - present, 0)` where the listing records a present
  SI. Underwriting fields come from the listing.
- **Administration.** As listed. Otherwise the policy's basis, or Named for a person
  whose new or increased cover exceeds the non-evidence limit by amount or age.
- **Premium.** Uses the slip's rates for the selected benefit year:
  - **Per $1,000 SI.** Rate × eligible SI.
  - **Family tier.** The employee row carries the tier's rate.
  - **Per member.** Dependant rows are priced too when the category covers them.
  - Missing rates stay blank and appear in the gaps sheet.
- **GST.** `premium × factor`. The factor comes from the product terms when they are
  set. Otherwise it comes from the slip's own rate heading: "GST Exempt" means no GST,
  and "Subj to GST" means 9%. If neither is stated, GST is blank and listed as a gap.

## Verification (7 October 2026)

**GAS (local).** The workbook was recalculated in Excel with no formula errors.
Headcount Summary reproduces the client's own listing:

| Product | Employees per category |
|---|---|
| GTL | 5 / 116 / 112 / 1,697 |
| GHS | 5 / 114 / 93 / 668 / 1,050 |
| GP | 5 / 1,925 |
| SP | 5 / 114 / 1,811 |
| Dental | 5 |

Directors' dependants: 8. A ninth shares another child's NRIC in the source file and
is skipped.

**CDL and STM.** Both export with generated layouts. PostgreSQL was not exercised
locally.
