/** Dependants table as the member sees it — shared by the portal
 * ("My dependants") and the broker's read-only employee-view preview. */
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Dependant } from "@/types";

const NAME_KEYS = ["name", "dependant_name", "full_name"];
const REL_KEYS = ["relationship", "relation", "rel", "dependant_type", "type"];
const DOB_KEYS = ["dob", "date_of_birth", "birth_date", "birthdate"];

function attr(dep: Dependant, keys: string[]): string {
  for (const key of keys) {
    const value = dep.attribute_values[key];
    if (value !== null && value !== undefined && value !== "") {
      return String(value).split(/[ T]/)[0];
    }
  }
  return "—";
}

/** Display name / relationship resolved from roster attribute_values — shared
 * with the enrollment dependant pickers so labels match this table. */
export function dependantName(dep: Dependant): string | null {
  const v = attr(dep, NAME_KEYS);
  return v === "—" ? null : v;
}

export function dependantRelationship(dep: Dependant): string | null {
  const v = attr(dep, REL_KEYS);
  return v === "—" ? null : v;
}

export function DependantStatusBadge({ status }: { status: string }) {
  if (status === "pending_approval") return <Badge variant="warn">Pending approval</Badge>;
  if (status === "rejected") return <Badge variant="error">Rejected</Badge>;
  return <Badge variant="good">Covered</Badge>;
}

export function DependantsTable({ rows }: { rows: Dependant[] }) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Relationship</TableHead>
            <TableHead>Date of birth</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((dep) => (
            <TableRow key={dep.id}>
              <TableCell className="font-medium text-foreground">
                {attr(dep, NAME_KEYS)}
              </TableCell>
              <TableCell className="capitalize">{attr(dep, REL_KEYS)}</TableCell>
              <TableCell>{attr(dep, DOB_KEYS)}</TableCell>
              <TableCell>
                <DependantStatusBadge status={dep.status} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
