import { usePortalTranslation } from "@/i18n/portal";
/** Searchable diagnosis combobox for the claim form. Options come from the
 * backend's curated ICD-10-based catalog (`/portal/claim-diagnoses`), scoped
 * to the selected product's claim setting (GP / specialist / hospital /
 * dental). "Other" switches to free text so an unlisted condition never
 * blocks a claim — the typed value is stored behind an "Other: " prefix so
 * brokers can spot it. */
import { useEffect, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { useClaimDiagnoses } from "@/api/portal";
import { Input } from "@/components/ui/input";

const OTHER_PREFIX = "Other: ";

export function DiagnosisPicker({
  productCode,
  value,
  onChange,
}: {
  productCode: string;
  value: string;
  onChange: (next: string) => void;
}) {
  const pt = usePortalTranslation();
  const isOther = value.startsWith(OTHER_PREFIX);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 200);
    return () => clearTimeout(t);
  }, [query]);

  // Fetch the complete scoped catalog once, so Chinese and English searches
  // match the displayed terms. The selected value remains the API's English label.
  const results = useClaimDiagnoses(open ? productCode : null, "", 200);
  const search = debounced.trim().toLocaleLowerCase();
  const items = (results.data?.items ?? []).filter(d => !search ||
    `${d.label} ${pt(d.label)} ${d.icd10 ?? ""}`.toLocaleLowerCase().includes(search));

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  if (isOther) {
    return (
      <div className="flex items-center gap-2">
        <Input
          value={value.slice(OTHER_PREFIX.length)}
          maxLength={200}
          placeholder={pt("Describe the diagnosis")}
          onChange={(e) => onChange(OTHER_PREFIX + e.target.value)}
        />
        <button
          type="button"
          className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => {
            onChange("");
            setQuery("");
          }}
        >
          {pt("Back to list")} </button>
      </div>
    );
  }

  const pick = (label: string) => {
    onChange(label);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={boxRef} className="relative">
      {value ? (
        <div className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground">
          <span className="truncate">{pt(value)}</span>
          <button
            type="button"
            aria-label={pt("Clear diagnosis")}
            className="ml-2 shrink-0 text-muted-foreground hover:text-foreground"
            onClick={() => {
              onChange("");
              setOpen(true);
            }}
          >
            <X className="size-3.5" />
          </button>
        </div>
      ) : (
        <div className="relative">
          <Input
            value={query}
            placeholder={pt("Start typing to search for options")}
            onFocus={() => setOpen(true)}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
          />
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        </div>
      )}

      {open && !value && (
        <ul className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-border bg-card py-1 shadow-md">
          {items.map((d) => (
            <li key={d.label}>
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left text-sm text-foreground hover:bg-muted"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(d.label)}
              >
                {pt(d.label)}
              </button>
            </li>
          ))}
          {results.data && items.length === 0 && (
            <li className="px-3 py-1.5 text-sm text-muted-foreground">
              {pt("No match — pick “Other” below.")} </li>
          )}
          <li className="border-t border-border">
            <button
              type="button"
              className="w-full px-3 py-1.5 text-left text-sm font-medium text-foreground hover:bg-muted"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onChange(OTHER_PREFIX);
                setOpen(false);
              }}
            >
              {pt("Other (not listed)")} </button>
          </li>
        </ul>
      )}
    </div>
  );
}
