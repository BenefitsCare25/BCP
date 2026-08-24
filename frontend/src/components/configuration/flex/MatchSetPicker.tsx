import { useId, useMemo, useState } from "react";
import { X, ChevronDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/ui/tooltip";
import type { VocabValue } from "@/types";

interface Props {
  label: string;
  hint?: string;
  /** Currently selected roster values (raw strings). */
  selected: string[];
  /** Distinct roster values to suggest, with headcounts. */
  options: VocabValue[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /**
   * Allow committing a value that isn't in `options` (Enter or Tab).
   *
   * OFF by default, and that default is load-bearing for flex tiers: a tier
   * match set that names a value absent from the roster matches nobody, so
   * free text there is always a mistake. The Insured picker turns it ON
   * because product setup routinely happens BEFORE the roster is uploaded —
   * there the vocabulary is legitimately empty and the broker must still be
   * able to record the entities the slip names.
   */
  allowCustom?: boolean;
  /** Suffix on an option already selected elsewhere (context-specific). */
  claimedNote?: string;
  /** Shown when there are no options at all. */
  emptyHint?: string;
  /** Tooltip on a chip that matches no option. */
  unknownNote?: string;
  /** Noun used in chip tooltips ("employee" → "3 employees on the roster"). */
  countNoun?: string;
  /**
   * Display text for a stored value, when the two differ.
   *
   * Default is identity — a roster vocabulary IS its own label. The member
   * selector stores cohort IDs and must show cohort names, and the search has
   * to match what the broker can see, so the label (not the opaque id) is what
   * both the chip and the filter use.
   */
  renderValue?: (value: string) => string;
  /** Search text when aliases or secondary labels should match too. */
  searchText?: (value: string) => string;
  /** Insurer and other catalog pickers do not have roster headcounts. */
  showCounts?: boolean;
  /** Render selected tokens inside the input's bordered field. */
  inlineChips?: boolean;
}

const norm = (s: string) => s.trim().toLowerCase();
// Cap the visible list so a large roster (100s of job titles) stays snappy;
// the search box narrows it, and a footer nudges the broker to keep typing.
const MAX_VISIBLE = 50;

/**
 * Searchable chip multi-select over a roster-anchored vocabulary.
 *
 * SELECT-ONLY by default (flex tier match sets): values come exclusively from
 * the roster vocabulary with headcounts, because a value that isn't on the
 * roster would match no employee. Pass `allowCustom` to also accept typed
 * values — see that prop for when that's correct.
 *
 * Already-selected values that are no longer on the roster (an AI-seeded term,
 * a changed roster, or a slip spelling the roster doesn't use) still show with
 * a "not found" warning so they can be reviewed and remapped.
 */
export function MatchSetPicker({
  label,
  hint,
  selected,
  options,
  onChange,
  placeholder,
  allowCustom = false,
  claimedNote = " · in another tier",
  emptyHint = "No roster values yet — upload a roster first.",
  unknownNote = "Not found on the current roster",
  countNoun = "employee",
  renderValue = (v) => v,
  searchText = renderValue,
  showCounts = true,
  inlineChips = false,
}: Props) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const inputId = useId();
  const listId = useId();
  const sel = selected ?? [];
  const has = (v: string) => sel.some((s) => norm(s) === norm(v));
  const countFor = (v: string) =>
    options.find((o) => norm(o.value) === norm(v))?.count;

  // Roster options not already selected, filtered by the search text. The
  // search runs over the DISPLAYED text: where value and label differ (cohort
  // ids), typing a cohort's name has to find it.
  const matches = useMemo(() => {
    const q = norm(draft);
    return options
      .filter((o) => !has(o.value))
      .filter((o) => !q || norm(searchText(o.value)).includes(q));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, draft, sel]);
  const shown = matches.slice(0, MAX_VISIBLE);
  const overflow = matches.length - shown.length;

  const addValue = (v: string) => {
    if (!v || has(v)) return;
    onChange([...sel, v]);
    setDraft("");
    setActive(-1);
  };
  const remove = (v: string) =>
    onChange(sel.filter((s) => norm(s) !== norm(v)));
  const selectedChips = sel.map((v) => {
    const c = countFor(v);
    const inRoster = c !== undefined;
    return (
      <span
        key={v}
        className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs ${
          inRoster
            ? "border-border bg-muted/40"
            : "border-warn/50 bg-warn-soft/40"
        }`}
        title={
          inRoster
            ? showCounts
              ? `${c} ${countNoun}${c === 1 ? "" : "s"} on the roster`
              : undefined
            : unknownNote
        }
      >
        <span className="text-foreground">{renderValue(v)}</span>
        {showCounts && inRoster && (
          <span className="text-muted-foreground">· {c}</span>
        )}
        <button
          type="button"
          onClick={() => remove(v)}
          aria-label={`Remove ${renderValue(v)}`}
          className="inline-flex size-6 items-center justify-center rounded-sm text-muted-foreground hover:text-error focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <X className="size-3" />
        </button>
      </span>
    );
  });

  return (
    <div className="space-y-1.5">
      <FieldLabel hint={hint || undefined} htmlFor={inputId}>
        {label}
      </FieldLabel>
      <div className="relative">
        <div
          className={
            inlineChips
              ? "flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-input bg-card px-2 py-1 shadow-sm focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/40"
              : undefined
          }
        >
          {inlineChips && selectedChips}
          <div
            className={inlineChips ? "relative min-w-40 flex-1" : "relative"}
          >
            <Input
              id={inputId}
              role="combobox"
              aria-expanded={open}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={
                open && active >= 0 && shown[active]
                  ? `${listId}-option-${active}`
                  : undefined
              }
              autoComplete="off"
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setActive(-1);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              // Blur closes the list; option clicks use onMouseDown (fires before blur)
              // so a selection isn't cancelled. Pending search text is discarded — it's
              // a filter, never a committed value.
              onBlur={() => setOpen(false)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setOpen(true);
                  setActive((a) =>
                    shown.length
                      ? Math.min(a < 0 ? 0 : a + 1, shown.length - 1)
                      : -1,
                  );
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) =>
                    shown.length ? Math.max(a - 1, 0) : -1,
                  );
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  const choice = shown[active] ?? shown[0];
                  if (choice) addValue(choice.value);
                  else if (allowCustom) addValue(draft.trim());
                } else if (
                  e.key === "Tab" &&
                  allowCustom &&
                  draft.trim()
                ) {
                  e.preventDefault();
                  addValue(draft.trim());
                } else if (e.key === "Escape") {
                  setOpen(false);
                } else if (e.key === "Backspace" && !draft && sel.length) {
                  remove(sel[sel.length - 1]);
                }
              }}
              placeholder={placeholder ?? "Search roster…"}
              className={
                inlineChips
                  ? "h-7 border-0 bg-transparent pr-8 shadow-none focus-visible:ring-0"
                  : "h-8 pr-8"
              }
            />
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          </div>
        </div>
        {open && (
          <ul
            id={listId}
            role="listbox"
            className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border bg-card p-1 shadow-md"
          >
            {shown.length === 0 ? (
              <li className="px-2 py-1.5 text-xs text-muted-foreground">
                {allowCustom && draft.trim()
                  ? `Press Enter to add “${draft.trim()}”.`
                  : options.length === 0
                    ? emptyHint
                    : "No matching roster value."}
              </li>
            ) : (
              shown.map((o, i) => (
                <li
                  id={`${listId}-option-${i}`}
                  key={o.value}
                  role="option"
                  aria-selected="false"
                >
                  <button
                    type="button"
                    // preventDefault keeps focus on the input so the list stays
                    // open for multi-select and blur doesn't cancel the click.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      addValue(o.value);
                    }}
                    onMouseEnter={() => setActive(i)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      i === active ? "bg-muted" : "hover:bg-muted/60"
                    }`}
                  >
                    <span className="truncate text-foreground">
                      {renderValue(o.value)}
                    </span>
                    {showCounts && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {o.count} staff{o.claimed ? claimedNote : ""}
                      </span>
                    )}
                  </button>
                </li>
              ))
            )}
            {overflow > 0 && (
              <li className="px-2 py-1.5 text-xs text-muted-foreground">
                …{overflow} more — keep typing to narrow.
              </li>
            )}
          </ul>
        )}
      </div>
      {!inlineChips && sel.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {selectedChips}
        </div>
      )}
      <span className="sr-only" aria-live="polite">
        {sel.length} selected
      </span>
    </div>
  );
}
