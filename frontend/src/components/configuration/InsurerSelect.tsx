import { useId } from "react";
import { useInsurers } from "@/api/insurers";
import { Input } from "@/components/ui/input";

/**
 * Insurer picker for a product form.
 *
 * Backed by a `<datalist>` rather than a strict `<Select>` on purpose: the
 * value stored on a product is a free-text string that also has to survive
 * placement-slip imports and legacy rows written before the catalog existed.
 * A hard select would make an unrecognised insurer unsaveable. So the catalog
 * supplies the options, and anything typed still saves — it just won't carry a
 * legal name until someone adds it under Attributes Setting → Insurers.
 */
export function InsurerSelect({
  value,
  onChange,
  id,
  placeholder = "AIA",
  className,
  extraOptions = [],
}: {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  placeholder?: string;
  className?: string;
  /** Names to offer alongside the catalog — e.g. values this client used on a
   *  previous setup that were never added as catalog entries. Anything already
   *  in the catalog is dropped so the list can't show a name twice. */
  extraOptions?: string[];
}) {
  const listId = `${useId()}-insurers`;
  const { data: insurers = [] } = useInsurers();
  const match = insurers.find(
    (i) => i.name.trim().toLowerCase() === value.trim().toLowerCase(),
  );
  const known = new Set(insurers.map((i) => i.name.trim().toLowerCase()));
  const extras = [
    ...new Set(
      extraOptions
        .map((o) => o.trim())
        .filter((o) => o && !known.has(o.toLowerCase())),
    ),
  ];

  return (
    <div className={className ?? "space-y-1"}>
      <Input
        id={id}
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
      />
      <datalist id={listId}>
        {insurers.map((i) => (
          <option key={i.id} value={i.name}>
            {i.legal_name ?? i.name}
          </option>
        ))}
        {extras.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
      {match?.legal_name && (
        <p className="text-xs text-muted-foreground">{match.legal_name}</p>
      )}
    </div>
  );
}
