import { useId } from "react";
import { useInsurers } from "@/api/insurers";
import { MatchSetPicker } from "@/components/configuration/flex/MatchSetPicker";
import { Input } from "@/components/ui/input";
import type { VocabValue } from "@/types";

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

function selectedInsurers(
  value: string | string[],
  configured: { name: string; aliases: string[] }[],
): string[] {
  const raw = Array.isArray(value) ? value : [value];
  const exact = !Array.isArray(value)
    ? configured.find(
        (item) =>
          item.name.trim().toLowerCase() === value.trim().toLowerCase(),
      )
    : undefined;
  const parts = exact
    ? [exact.name]
    : raw
        .flatMap((item) => item.split(/[,;\n]+/))
        .map((item) => item.trim())
        .filter(Boolean);
  const seen = new Set<string>();
  const selected: string[] = [];
  for (const part of parts) {
    const normalized = part.toLowerCase();
    const match = configured.find(
      (item) =>
        item.name.trim().toLowerCase() === normalized ||
        item.aliases.some((alias) => alias.trim().toLowerCase() === normalized),
    );
    const name = match?.name.trim() || part;
    const key = name.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      selected.push(name);
    }
  }
  return selected;
}

/** Searchable, select-only insurer tokens for quotation recipients. */
export function InsurerMultiSelect({
  value,
  onChange,
  label = "Insurers",
}: {
  value: string | string[];
  onChange: (value: string[]) => void;
  label?: string;
}) {
  const query = useInsurers();
  const insurers = query.data ?? [];
  const selected = selectedInsurers(value, insurers);
  const options: VocabValue[] = insurers.map((insurer) => ({
    value: insurer.name,
    count: 0,
  }));
  const byName = new Map(
    insurers.map((insurer) => [insurer.name.trim().toLowerCase(), insurer]),
  );
  const emptyHint = query.isLoading
    ? "Loading insurers…"
    : query.isError
      ? "Could not load Insurer Settings. Refresh and try again."
      : "No insurers configured. Add one under Settings → Insurers.";

  return (
    <MatchSetPicker
      label={label}
      hint="Select every insurer that should receive this product for quotation. The product sheet will be included in each selected insurer's workbook."
      selected={selected}
      options={options}
      onChange={onChange}
      placeholder="Search configured insurers…"
      allowCustom={false}
      emptyHint={emptyHint}
      unknownNote="Not found in Insurer Settings. Remove it or add the insurer in Settings before sending quotations."
      showCounts={false}
      inlineChips
      searchText={(name) => {
        const insurer = byName.get(name.trim().toLowerCase());
        return [name, insurer?.legal_name, ...(insurer?.aliases ?? [])]
          .filter(Boolean)
          .join(" ");
      }}
    />
  );
}
