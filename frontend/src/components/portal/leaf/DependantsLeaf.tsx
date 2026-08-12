/** The people on the member's leaf.
 *
 * One mount per person rather than a four-column table. The table was the
 * portal's clearest mobile failure: four columns that never collapse, a status
 * carried only by a soft badge, and a date of birth given the same weight as
 * the person's name. Here the name is the heading, the state is struck, and the
 * supporting facts sit under it — which is the same information at one column
 * wide.
 *
 * Shared by the member's own page and the broker's employee-view preview, so
 * the two provably cannot drift. */
import type { Dependant } from "@/types";
import {
  dependantDob,
  dependantName,
  dependantRelationship,
} from "@/lib/dependant";
import { Mount, MountRow } from "./Mount";
import { Strike } from "./Strike";
import { formatDay } from "./date";

/** The state as the member experiences it, which is NOT the row's status.
 *
 * "Covered" used to be printed for every approved row, on the assumption that
 * an approved dependant is always swept into the plans. That stopped being
 * true the moment cover could be set per person — an enrollment election that
 * leaves someone out, or a broker taking one side of a dual-covered life off
 * cover — and the member was still told they were covered while no plan would
 * pay for them. Both callers resolve `covered` from the benefit statement, the
 * broker preview included, so the two surfaces answer identically; it stays
 * optional so a caller without a statement to read falls back to the row's own
 * status rather than asserting cover it cannot see. */
function DependantState({
  status,
  covered,
}: {
  status: string;
  covered?: boolean;
}) {
  if (status === "pending_approval")
    return <Strike tone="pending">Waiting for approval</Strike>;
  if (status === "rejected") return <Strike tone="rejected">Not approved</Strike>;
  if (covered === false) return <Strike tone="rejected">Not covered</Strike>;
  return <Strike tone="approved">Covered</Strike>;
}

export function DependantsLeaf({
  rows,
  cover,
}: {
  rows: Dependant[];
  /** dependant id → the PLANS covering them, named. Omit when unresolvable
   *  (the value is the answer to "what am I covered for", so an empty entry
   *  and a missing one mean different things: nothing, versus not known). */
  cover?: Map<string, string[]>;
}) {
  if (rows.length === 0) {
    return (
      <Mount label="No one added yet">
        <p className="text-row text-label">
          Family members you add here can be covered under the plans that
          include dependants. Your HR team approves each one before their cover
          starts.
        </p>
      </Mount>
    );
  }

  return (
    <ul className="space-y-3">
      {rows.map((dep) => {
        const name = dependantName(dep);
        const relationship = dependantRelationship(dep);
        const dob = dependantDob(dep);
        const plans = cover?.get(dep.id);
        return (
          <Mount
            key={dep.id}
            as="li"
            label={name ?? "Family member"}
            gloss={relationship ? <span className="capitalize">{relationship}</span> : null}
            aside={
              <DependantState
                status={dep.status}
                covered={plans ? plans.length > 0 : undefined}
              />
            }
          >
            {(dob || plans) && (
              <dl>
                {/* Written the way the member would say it. The roster's
                    "1966-05-21" is a database value, and the portal prints
                    every other date through `formatDay` — a person's own
                    date of birth is the last place to break that. */}
                {dob && <MountRow term="Date of birth">{formatDay(dob)}</MountRow>}
                {/* WHAT they are covered for, which is the question this page
                    exists to answer and the one it did not. A relationship and
                    a date of birth are facts the member supplied; the plans are
                    the thing they came here to read. Named in full, never as
                    GHS/GMM — a code is an unglossed term on a member surface. */}
                {plans && plans.length > 0 && (
                  <MountRow term="Covered under">{plans.join(", ")}</MountRow>
                )}
                {plans && plans.length === 0 && dep.status === "active" && (
                  <MountRow term="Covered under">
                    No plan covers them yet — your HR team can add them.
                  </MountRow>
                )}
              </dl>
            )}
          </Mount>
        );
      })}
    </ul>
  );
}
