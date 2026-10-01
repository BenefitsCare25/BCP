/** Section C of the paper form — the member's family, with each person's
 * eligibility stated before they are put on a plan.
 *
 * Who is COVERED is still chosen on each product's own step (that is where the
 * price of covering them is shown). This step answers the questions the paper
 * form's notes asked the member to work out for themselves: is this person
 * eligible, and — for someone added during this enrolment, who can't be
 * elected until the broker verifies them — which plans should they join. */
import { Link } from "@tanstack/react-router";
import type { ProductTierSet } from "@/api/enrollment";
import type { FormDependant } from "@/api/enrollmentForms";
import { GoArrow, goLinkClass } from "@/components/portal/leaf/Action";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { Strike } from "@/components/portal/leaf/Strike";
import { formatDay } from "@/components/portal/leaf/date";
import { useCompany } from "@/components/portal/useCompany";
import { choiceControl, choiceRowClass } from "../choiceRow";
import type { PendingRequests } from "./formMath";

function describe(dep: FormDependant): string {
  const parts = [dep.relationship ?? "Family member"];
  if (dep.age_next_birthday !== null) parts.push(`age ${dep.age_next_birthday} next birthday`);
  if (dep.dob) parts.push(`born ${formatDay(dep.dob)}`);
  return parts.join(" · ");
}

export function FamilyMount({
  dependants,
  eligibilityNotes,
  familyProducts,
  pending,
  disabled,
  onPendingChange,
}: {
  dependants: FormDependant[];
  eligibilityNotes: string[];
  /** Plans the member keeps with voluntary family cover — empty when the
   *  period allows no family changes. */
  familyProducts: ProductTierSet[];
  pending: PendingRequests;
  disabled: boolean;
  onPendingChange: (next: PendingRequests) => void;
}) {
  const company = useCompany();
  // Plans this person could join: voluntary family cover the member keeps,
  // inside that plan's own age window.
  const requestable = (dep: FormDependant) =>
    familyProducts.filter((ts) => !dep.ineligible_products.includes(ts.product_code));
  const toggle = (depId: string, code: string, on: boolean) => {
    const current = pending[depId] ?? [];
    onPendingChange({
      ...pending,
      [depId]: on ? [...new Set([...current, code])] : current.filter((c) => c !== code),
    });
  };

  return (
    <Mount
      as="article"
      rise={false}
      label="Your family"
      gloss="Who can be covered, and anyone you've added who is still being checked."
    >
      {dependants.length === 0 ? (
        <p className="text-row text-label">You have no family members on record.</p>
      ) : (
        <ul className="divide-y divide-hairline/75">
          {dependants.map((dep) => (
            <li key={dep.id} className="flex flex-col gap-2 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-row font-medium text-record">{dep.name ?? "Unnamed"}</p>
                  <p className="text-row text-label">{describe(dep)}</p>
                  {dep.eligibility_note && (
                    <p
                      className={
                        dep.eligible ? "text-row text-label" : "text-row text-strike-pending"
                      }
                    >
                      {dep.eligibility_note}
                    </p>
                  )}
                </div>
                <Strike
                  tone={dep.status === "pending" ? "review" : dep.eligible ? "approved" : "pending"}
                  className="shrink-0"
                >
                  {dep.status === "pending"
                    ? "Being checked"
                    : dep.eligible
                      ? "Eligible"
                      : "Not eligible"}
                </Strike>
              </div>

              {dep.status === "pending" && dep.eligible && requestable(dep).length > 0 && (
                <fieldset className="flex flex-col">
                  <legend className="text-row text-label">
                    Enrol {dep.name ?? "them"} on, once checked:
                  </legend>
                  {requestable(dep).map((ts) => {
                    const on = (pending[dep.id] ?? []).includes(ts.product_code);
                    return (
                      <label key={ts.product_code} className={choiceRowClass(on, "items-center")}>
                        <input
                          type="checkbox"
                          className={choiceControl}
                          checked={on}
                          disabled={disabled}
                          onChange={(e) => toggle(dep.id, ts.product_code, e.target.checked)}
                        />
                        <span className="text-row text-record">
                          {ts.product_name ?? ts.product_code}
                        </span>
                      </label>
                    );
                  })}
                </fieldset>
              )}
            </li>
          ))}
        </ul>
      )}

      {!disabled && (
        <Link
          to="/portal/$company/dependants"
          params={{ company }}
          className={goLinkClass({ brand: true })}
        >
          Add a family member (spouse, newborn or child)
          <GoArrow brand />
        </Link>
      )}

      {eligibilityNotes.length > 0 && (
        <>
          <MountRule />
          <div>
            <p className="text-row font-medium text-record">Who counts as family</p>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-row text-label">
              {eligibilityNotes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        </>
      )}
    </Mount>
  );
}
