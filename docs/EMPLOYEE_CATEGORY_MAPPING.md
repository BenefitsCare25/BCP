# Employee category mapping

## Purpose

Employee-category setup translates each placement-slip cohort into a company-scoped
rule over that company's active employee roster. The same product can use different
plans, titles, grades, entities, and participation models at different companies, so
the system must not rely on a global list of category names.

The placement slip remains authoritative for benefits and stated eligibility. The
roster is authoritative for the employee fields and values that can be evaluated.
Confirmed mappings are reusable only inside the same client company.

## Why the CDL screen showed 122 reviews

The 22 August 2026 production baseline was:

- 135 employee-category rows extracted, plus 18 dependant-only rows excluded.
- 3 roster validated, 102 marked needs review, and 20 unmapped.
- 491 active employees and 11 products mapped to plans.

Most of the 102 review flags did not mean that the employee rule was wrong. They
came from two validation policies that were too strict:

1. Alternative plan/tier rows for the same employee cohort used the same rule. The
   validator treated those legitimate siblings as competing employee cohorts.
2. Any difference between the slip's stated headcount and the current active roster
   changed an otherwise valid rule to `needs_review`. Slip counts may be historical,
   coverage-tier counts, or based on a different census date.

The audit also found real mapping problems that must continue to require review:

- company concepts with no roster field/value, such as authorised travellers,
  firefighters, manual/non-manual staff, or a named person;
- unresolved title hierarchies such as "Senior Vice President and above";
- rules that refer to an empty roster attribute;
- a rule that matches no active employee when the slip states a positive headcount;
- equally specific matches across genuinely different employee cohorts.

One deterministic-parser error mapped STM's `S$1,600` currency marker to the
single-character family-status value `S`. Generic text matching must therefore not
infer single-character roster values. Explicit grade/code clauses remain supported
by the dedicated grade parser.

## Dynamic mapping model

### 1. Separate employee cohorts from plan tiers

A cohort answers **who is eligible**. A category row also contains assignment data
that answers **which plan or tier applies**. Several plan rows may reference the same
cohort without creating an eligibility conflict.

The cohort identity is the normalized category signature, scoped by company,
product, and insured entity. Plan prefixes, option suffixes, and dependant boilerplate
do not change that identity. The live matcher still selects one baseline category per
product; the enrollment tier service exposes voluntary siblings for that cohort.

### 2. Compile only against company-owned vocabulary

The deterministic compiler uses:

- populated non-PII employee attributes and their observed values;
- configured enum values for valid future bands;
- explicit grade ranges, work-pass types, exclusions, and all-employee/remainder
  grammar;
- a confirmed company mapping profile or prior-year confirmed rule when available.

It never invents a field, title, grade, hierarchy, entity, or enum value.

### 3. Use four mapping states

- `validated`: the rule is structurally valid, has no unresolved/blocking condition,
  and was evaluated against an active roster.
- `proposed`: the rule is structurally complete but no active roster is available.
- `needs_review`: the rule has unresolved wording, an invalid field/value, a
  positive-headcount zero match, or ambiguity with a different cohort.
- `unmapped`: no safe employee rule exists.

Headcount drift and "configured value currently unused" are advisory warnings. They
remain visible to the broker but do not make a correct rule fail validation.

### 4. Validate by cohort, not by category row

For every product and employee, rules are ranked by the live matcher's confirmed-first
and specificity precedence. Equal-ranked plan siblings with the same cohort identity
receive the same cohort match count. Overlap is blocking only when equal-ranked rules
belong to different cohort identities.

### 5. Reconcile uploads without duplicating reviewed work

Re-upload clears prior unreviewed generated rows. Reviewed slip rows are matched by
product, plan code, normalized category signature, and insured entity, then refreshed
in place with the new slip's participation, financial assignment, and source row.
Their confirmed/manual rule and audit identity survive. This prevents a real slip
from displaying both an old confirmed row and a new unreviewed duplicate.

### 6. Keep AI as a bounded fallback

`Suggest rule with AI` receives authoritative slip wording, non-PII roster vocabulary,
product/plan context, sibling descriptions, and the deterministic candidate. Gemini
must return the forced structured rule and may only use supplied fields and values.
Output is validated locally before any category change is saved, and broker
confirmation remains required.

Gemini 3.5 thinking is explicitly bounded to `MINIMAL` for this small structured task.
Without that setting, dynamic thinking consumed the 1,024-token response budget and
the forced tool payload was truncated.

## Missing roster behavior

A company with no active employee roster can receive deterministic or confirmed
company proposals, but it cannot receive roster match counts or `validated` status.
The workbench must show this limitation prominently. A fake roster must never be
created to make validation pass.

## Production QA baseline

Real placement slips uploaded through the production `Upload slip` UI on 22 August
2026 produced these pre-fix results:

| Company | Extracted | Roster | Validated | Proposed | Review | Unmapped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CDL | 135 | 491 | 3 | 0 | 102 | 20 |
| STM | 28 | 4,806 | 3 | 0 | 19 | 6 |
| MCIL | 28 | 0 | 0 | 0 | 11 | 26 |

MCIL displayed 37 mapping rows after extracting 28 because reviewed rows from a prior
upload were retained beside fresh rows. MCIL has no active production roster, so its
rules can only be proposed or reviewed until a real employee listing is supplied.

Retesting after deployment must re-upload the same three reference slips, rebuild
proposals, exercise AI suggestion, and record the new state counts. No generated or
fake workbook is permitted.
