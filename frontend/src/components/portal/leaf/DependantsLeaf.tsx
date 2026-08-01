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

function DependantState({ status }: { status: string }) {
  if (status === "pending_approval")
    return <Strike tone="pending">Waiting for approval</Strike>;
  if (status === "rejected") return <Strike tone="rejected">Not approved</Strike>;
  return <Strike tone="approved">Covered</Strike>;
}

export function DependantsLeaf({ rows }: { rows: Dependant[] }) {
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
        return (
          <Mount
            key={dep.id}
            as="li"
            label={name ?? "Family member"}
            gloss={relationship ? <span className="capitalize">{relationship}</span> : null}
            aside={<DependantState status={dep.status} />}
          >
            {dob && (
              <dl>
                {/* Written the way the member would say it. The roster's
                    "1966-05-21" is a database value, and the portal prints
                    every other date through `formatDay` — a person's own
                    date of birth is the last place to break that. */}
                <MountRow term="Date of birth">{formatDay(dob)}</MountRow>
              </dl>
            )}
          </Mount>
        );
      })}
    </ul>
  );
}
