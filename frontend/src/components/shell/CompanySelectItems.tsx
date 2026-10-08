import { SelectGroup, SelectItem, SelectLabel } from "@/components/ui/select";
import { useCompanyGroups } from "./companyGroups";

/** The caller's companies as `Select` options — grouped under their broker
 *  firm's name when they span several firms, a flat list otherwise. */
export function CompanySelectItems() {
  const { grouped, groups } = useCompanyGroups();
  if (!grouped) {
    return (
      <>
        {groups.flatMap((g) => g.clients).map((c) => (
          <SelectItem key={c.id} value={c.id}>
            {c.name}
          </SelectItem>
        ))}
      </>
    );
  }
  return (
    <>
      {groups.map((g) => (
        <SelectGroup key={g.firmId}>
          <SelectLabel>{g.firmName}</SelectLabel>
          {g.clients.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
        </SelectGroup>
      ))}
    </>
  );
}
