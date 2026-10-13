import type { ListingPeople } from "@/api/employeeListing";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { SectionLabel } from "@/components/ui/section-label";
import type { AdcPreview } from "@/types";
import { byRecordType, count, peopleText } from "./format";

/**
 * Who the file lists — the only place its totals appear — then what applying
 * changes on the roster. Employees and dependants are counted apart: a
 * listing holds both on one sheet, so a single "260 additions" hid that 81 of
 * them were dependants. A first upload, where everyone is new, says so rather
 * than repeating the totals.
 */
export function ReviewSummary({
  people,
  members,
}: {
  people: ListingPeople;
  members: AdcPreview;
}) {
  const relations = [
    people.spouses ? count(people.spouses, "spouse") : null,
    people.children ? count(people.children, "child", "children") : null,
  ].filter(Boolean);
  return (
    <dl className="grid gap-x-4 gap-y-2 rounded-lg border border-border px-3 py-2.5 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:items-baseline">
      <SectionLabel as="dt">In this file</SectionLabel>
      <dd className="text-sm text-foreground">
        <span className="tabular-nums">{count(people.employees, "employee")}</span>
        <span className="text-muted-foreground"> · </span>
        <span className="tabular-nums">{count(people.dependants, "dependant")}</span>
        {relations.length > 0 && (
          <span className="text-muted-foreground"> ({relations.join(", ")})</span>
        )}
      </dd>
      <SectionLabel as="dt">Roster</SectionLabel>
      <dd className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
        <RosterChanges people={people} members={members} />
      </dd>
    </dl>
  );
}

function RosterChanges({ people, members }: { people: ListingPeople; members: AdcPreview }) {
  const added = byRecordType(members.additions);
  const movements: [string, BadgeProps["variant"], string | null][] = [
    ["Add", "good", peopleText(added)],
    ["Update", "info", peopleText(byRecordType(members.changes))],
    ["End cover", "warn", peopleText(byRecordType(members.deletions))],
  ];
  const changed = movements.filter(([, , text]) => text !== null);
  const everyoneNew =
    changed.length === 1 &&
    added.employees === people.employees &&
    added.dependants === people.dependants;
  if (everyoneNew) {
    return <span className="text-foreground">Everyone is new — all will be added</span>;
  }
  if (changed.length === 0) {
    return <span className="text-muted-foreground">No changes — the roster already matches</span>;
  }
  return (
    <>
      {changed.map(([label, variant, text]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <Badge variant={variant}>{label}</Badge>
          <span className="tabular-nums text-foreground">{text}</span>
        </span>
      ))}
    </>
  );
}
