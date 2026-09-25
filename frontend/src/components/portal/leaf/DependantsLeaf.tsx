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
import type { BenefitStatement, CoverageLine, Dependant } from "@/types";
import {
  dependantDob,
  dependantName,
  dependantRelationship,
} from "@/lib/dependant";
import { Mount } from "./Mount";
import { buildCareRoutes } from "./careRoutes";
import { careTone } from "./careTone";
import { isEmployeeLine } from "../memberVisibility";
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

/** The benefits one person holds, as the same coloured tags Home uses. */
function CareTags({ lines }: { lines: CoverageLine[] }) {
  const routes = buildCareRoutes(lines);
  if (routes.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-2.5 gap-y-3 pt-1">
      {routes.map((route) => (
        <span key={route.key} className={`clay-tag !text-[10px] !normal-case !tracking-normal tone-${careTone(route.key)}`}>
          {route.title}
        </span>
      ))}
    </div>
  );
}

export function DependantsLeaf({
  rows,
  statement,
  selfName,
}: {
  rows: Dependant[];
  /** The member's statement, to name what each person is covered for. Omit
   *  while it is unresolvable — an absent list is not "covered for nothing". */
  statement?: BenefitStatement;
  /** The member's own name: the family starts with them. */
  selfName?: string;
}) {
  const selfLines = statement ? statement.coverage.filter(isEmployeeLine) : null;
  const linesFor = (id: string) =>
    statement ? statement.coverage.filter((line) => line.covered_dependants.some((person) => person.id === id)) : null;

  return (
    <ul className="space-y-3">
      {selfName && selfLines && (
        <Mount as="li" label={selfName} gloss="You" aside={<Strike tone="approved">Covered</Strike>}>
          <CareTags lines={selfLines} />
        </Mount>
      )}
      {rows.length === 0 && (
        <Mount as="li" label="No one added yet">
          <p className="text-row text-label">
            Add your spouse or children to be covered under the plans that
            include family. Your HR team approves each one first.
          </p>
        </Mount>
      )}
      {rows.map((dep) => {
        const name = dependantName(dep);
        const relationship = dependantRelationship(dep);
        const dob = dependantDob(dep);
        const lines = linesFor(dep.id);
        return (
          <Mount
            key={dep.id}
            as="li"
            label={name ?? "Family member"}
            gloss={
              <span className="capitalize">
                {[relationship, dob ? `born ${formatDay(dob)}` : null].filter(Boolean).join(" · ")}
              </span>
            }
            aside={<DependantState status={dep.status} covered={lines ? lines.length > 0 : undefined} />}
          >
            {lines && lines.length > 0 && <CareTags lines={lines} />}
            {lines && lines.length === 0 && dep.status === "active" && (
              <p className="text-row text-label">No plan covers them yet — your HR team can add them.</p>
            )}
          </Mount>
        );
      })}
    </ul>
  );
}
