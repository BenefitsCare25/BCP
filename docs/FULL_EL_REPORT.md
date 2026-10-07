# Full Employee Listing

Reports Center → Policy Admin → Internal registers → **Full Employee Listing (EL)**.
This is a separate company-wide workbook. Member Register and insurer submissions
remain available. The same tenant authorization, masking permission and export audit
apply. Full EL downloads are current snapshots; they are not retained submission versions.

## Workbook

| Sheet | Content |
| --- | --- |
| Basis of Cover | Company, benefit period, product periods, administration, eligibility, category cover/rates, GST and underwriting limits |
| Full EL | Employment details, lifecycle dates, insurer member IDs and dynamic product coverage, underwriting and annual premium columns |
| Dependants | Staff ID links, individual identity, actual resolved cover and per-life underwriting |
| Source Setup | Saved setup fields, schedules, selected/historical rate periods, terms, endorsements and source references |
| Headcount & Annual Premium | Product, insurer, entity, category, plan, family, administration and currency groups |
| Setup & Data Gaps | Missing setup, rates, mappings, identity fields, source documents and unresolved calculation rules |
| Declaration | Unsigned company declaration for completion before submission |

All / Active only and NRIC masking apply to the whole workbook. Source wording and
remarks are also scanned for ordinary Singapore national-ID strings in masked exports.
Dates and money are native Excel values. Untrusted strings are formula-escaped.

## Rules and source review

Header & Policy contains the Full EL age convention/reference, premium SI basis,
currency, maximum SI and administration exception fields. Explicit ANB/ALB wording,
currency in selected rate headers and unambiguous per-person limits can prefill
these fields from this company's retained extraction. Benefit payout wording does
not establish the premium SI basis. A name-basis movement clause does not override
a headcount administration header automatically.

Setup confirmation fills missing NEL amount/age and explicit GST-exempt treatment
from unambiguous saved source wording. It preserves existing terms and explicit
edits/clears, uses the existing term-update audit and runs in the same transaction.
This also repairs the old extraction case `200,000 or age next birthday 70` without
interpreting the trailing digits of a monetary amount as an age.

Unconfirmed setup or category mapping withholds annual premiums. Rates for a
different renewal are withheld. Unknown GST leaves gross premium blank. Missing
prices never become zero or a complete total. Summary rows label known subtotals
and show a complete gross total only when every premium unit is priced.

Premiums are annual, without proration. Family-tier/flat household premiums appear
once on the employee row; linked medical dependants do not duplicate them. Coverage
must overlap the product and benefit-year windows. Earnings-rated and policy-level
group premiums remain in Basis of Cover rather than being copied to individuals.
Substandard dependant medical cover requires a reviewed family price; standard
family pricing is not silently applied to that exception.

Underwriting details include historical standard accepted SI, loading wording and
new/renewal letter dates to the member and insurer. An individual annual premium
requires explicit insurer confirmation and currency. It is invalidated for reporting
when SI or the underwriting decision changes. Correspondence-only edits do not reconfirm the price or reset the
decision date. Loading text is informational, never guessed into a numeric surcharge.

The workbook shows recorded movement/lifecycle fields. It does not infer A/D/C
against a previous Full EL. Existing insurer submission history remains the facility
for submission-to-submission movement comparison.

## Existing company data

The sample workbooks are layout references, not pricing authority. The primary GAS
context and the separately labelled mapping-review context are never merged by a
report. WDNS is not present in the current shared local database. GAS draft setup,
missing rates/mappings and unretained original placement slips are exposed as gaps;
the generator cannot manufacture the missing source data or approve the setup.

## Release and verification

Migration `e9a1b3c5d7f0` adds nullable `underwriting_cases.report_details` in SQLite,
the PostgreSQL public schema and each existing firm schema. Apply it before the API
release. The migration preserves existing records and supports newly provisioned
firm schemas through the ORM model. For production rollback, leave the additive
column in place and roll back application code; do not drop populated reporting data.

Locally verified: the additive migration preserved counts and foreign-key integrity;
upgrade/idempotence/downgrade preserved 5,000 synthetic rows; a browser download for
GAS contained 1,926 employees and all seven sheets. Isolated checks covered annual
SI rating/caps, ANB boundaries, family/GST reconciliation, group totals, missing
prices, substandard premiums, stale confirmations, formula protection and masking.
The focused backend suite passed 169 tests and the frontend production build passed.
PostgreSQL execution remains a release check: the local Docker daemon was unavailable;
the migration's public/firm-schema paths were reviewed, but not run against PostgreSQL.
No real member, eligibility or underwriting decision was changed for verification.
