/** Who the claim is for, and what it is for.
 *
 * There is no separate "claim category" step — the claim-type dropdown is
 * grouped Outpatient / Inpatient / Other insurance / Flexible Benefits, and
 * both `claim_kind` AND `sub_type` are DERIVED from the chosen entry, which
 * makes a category/type/sub-type mismatch structurally impossible. Entries come
 * from `/portal/coverage-options`, so the list is plan-aware.
 *
 * The claimant comes FIRST because it filters the type list: a dependant sees
 * the flex categories plus only the insured products that cover them. */
import { Field, FieldGroup, leafControl } from "@/components/portal/leaf/Field";
import { FLEX_PREFIX, type InsuredGroupKey } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

export function ClaimTypeFields({ form }: { form: NewClaimForm }) {
  const {
    dependants,
    flex,
    insuredGroups,
    groupLabels,
    selectedProduct,
    memberName,
  } = form;

  return (
    <>
      {form.hasDependants && (
        <Field label="Who is this claim for?">
          {(p) => (
            <select
              {...p}
              className={leafControl}
              value={form.dependantId}
              onChange={(e) => form.changeClaimant(e.target.value)}
            >
              {/* The member by NAME, not "Myself": every other row in this
                  list is a person named, so the first one was the only entry
                  phrased differently from its siblings. */}
              <option value="">{memberName}</option>
              {dependants.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name ?? "Dependant"}
                  {d.relationship ? ` (${d.relationship})` : ""}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}

      {/* A `<label for>` pointing at a control that isn't rendered labels
          nothing — which is the defect `Field` exists to make unrepresentable.
          The no-types case is a group with a sentence, not a labelled control. */}
      {form.noTypesForClaimant ? (
        // The error has to be wired on BOTH branches. `validateClaim` still
        // sets `claim_type` here — this branch is exactly the state in which it
        // cannot be satisfied — so leaving it off rendered "Fix the highlighted
        // fields" above a form with nothing highlighted, and the one blocker on
        // screen looking like ordinary help text.
        <FieldGroup label="Claim type" error={form.fieldErrors.claim_type}>
          <p className="text-row text-label">
            This dependant has no claimable benefits — pick a different
            claimant.
          </p>
        </FieldGroup>
      ) : (
        <Field label="Claim type" required error={form.fieldErrors.claim_type}>
          {(p) => (
            <select
              {...p}
              className={leafControl}
              value={form.selection}
              onChange={(e) => form.changeSelection(e.target.value)}
            >
              <option value="">Select an option</option>
              {(Object.keys(groupLabels) as InsuredGroupKey[]).map(
                (key) =>
                  insuredGroups[key].length > 0 && (
                    <optgroup key={key} label={groupLabels[key]}>
                      {insuredGroups[key].map((entry) => (
                        <option key={entry.value} value={entry.value}>
                          {entry.label}
                        </option>
                      ))}
                    </optgroup>
                  ),
              )}
              {form.hasFlex && (
                <optgroup label="Flexible Benefits">
                  {flex?.categories.map((c) => (
                    <option key={c.name} value={`${FLEX_PREFIX}${c.name}`}>
                      {c.name}
                      {c.sub_limit != null ? ` (up to ${c.sub_limit})` : ""}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          )}
        </Field>
      )}

      {/* Insurer member ID — read-only, keyed off the selected claim type's
          product/insurer (from the roster). Hidden when none on file. */}
      {selectedProduct?.insurer_member_id && (
        <div className="flex flex-wrap items-baseline gap-x-2 rounded-control bg-bar/70 px-3 py-2">
          <span className="leaf-label">Insurer member ID</span>
          <span className="text-row font-medium text-record">
            {selectedProduct.insurer_member_id}
          </span>
          {selectedProduct.insurer && (
            <span className="text-row text-label">
              · {selectedProduct.insurer}
            </span>
          )}
        </div>
      )}

      {/* Specialist claims: first vs follow-up visit decides the referral rule
          (first must attach a letter; follow-up reuses the latest on file,
          prompting only when none is tracked). */}
      {form.needsReferral && (
        <Field
          label="Is this a first visit or follow-up?"
          required
          error={form.fieldErrors.visit_type}
        >
          {(p) => (
            <select
              {...p}
              className={leafControl}
              value={form.visitType}
              onChange={(e) => form.setVisitType(e.target.value)}
            >
              <option value="">Select an option</option>
              <option value="first">First visit</option>
              <option value="follow_up">Follow-up visit</option>
            </select>
          )}
        </Field>
      )}
    </>
  );
}
