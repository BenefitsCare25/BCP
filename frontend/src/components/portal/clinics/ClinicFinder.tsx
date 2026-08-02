/** The page's controls, as two labelled record rows rather than a toolbar.
 *
 *     WHERE YOU ARE   [◎ Use my location]  or  [Postal code]
 *     ─────────────────────────────────────────────────────────────
 *     WHAT TO SHOW    [All][GP · Singapore][GP · JB]   [name] [areas]
 *
 * **Every control is sized to what goes in it.** The postal box is ~9rem
 * because a Singapore postal code is six digits; the name box is ~13.5rem
 * because that is a clinic name; the area list is ~9.25rem because that is
 * "Woodlands". The previous version gave the search field the full column,
 * which is an argument that the page wants an essay — and it is the same
 * defect, one level down, as the full-bleed action pills this rewrite removed.
 *
 * **The origin is a question, not furniture.** Answered, the whole left-hand
 * group is REPLACED IN PLACE by a chip you can clear, so the control does not
 * sit there spent for the rest of the visit.
 *
 * **Two boxes, and the labels are what separate them.** Both accept something
 * address-shaped and they do different things — one sets the origin the list
 * is ranked from, the other filters the list — so they sit under different
 * labels at different widths.
 *
 * **The filter box is NOT hijacked when it holds six digits.** That looks like
 * a helpful redirect and it deletes a working query: the backend's `_matches_q`
 * searches `postal_code` and `address` as well as the name, so a postal code
 * typed here answers "is the clinic in my block on the panel?" — which is a
 * question a member actually has. Routing it to the geocoder instead would
 * also wipe the box mid-typing for anyone entering a longer number.
 *
 * The `<select>` is native, not the shared Radix one: Radix portals its
 * listbox to `document.body`, outside `.leaf`, so it would render in the
 * broker's tokens on the member's screen. A native select also gets the
 * platform picker on a phone. */
import { useEffect, useId, useState } from "react";
import { Loader2, LocateFixed, Search, X } from "lucide-react";
import type { ClinicTypeFacet } from "@/api/panelListings";
import { actionClass } from "@/components/portal/leaf/Action";
import { leafControl } from "@/components/portal/leaf/Field";
import { Mount, MountRule } from "@/components/portal/leaf/Mount";
import { cn } from "@/lib/cn";

export const ALL_AREAS = "__all__";
/** Mirrors the backend `Query(max_length=128)` so a long paste cannot 422. */
export const MAX_QUERY_LENGTH = 128;
/** Exactly six digits — a complete Singapore postal code, which is the whole
 * submit gesture for the origin box. */
const POSTAL_CODE = /^\d{6}$/;

const locateClass = actionClass("quiet", { block: "phone", className: "px-4" });

export interface OriginState {
  label: string;
  postal: string | null;
}

export type OriginStatus =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "error"; message: string };

export function ClinicFinder({
  facets,
  typeKey,
  onTypeKey,
  areas,
  area,
  onArea,
  search,
  onSearch,
  origin,
  status,
  onGps,
  onPostal,
  onClearOrigin,
}: {
  facets: ClinicTypeFacet[];
  typeKey: string;
  onTypeKey: (key: string) => void;
  areas: string[];
  area: string;
  onArea: (area: string) => void;
  search: string;
  onSearch: (value: string) => void;
  origin: OriginState | null;
  status: OriginStatus;
  onGps: () => void;
  onPostal: (code: string) => void;
  onClearOrigin: () => void;
}) {
  const postalId = useId();
  const searchId = useId();
  const areaId = useId();
  const [postal, setPostal] = useState("");
  const busy = status.kind === "busy";

  // Six digits is the whole submit gesture — there is no "Set" button to leave
  // sitting disabled beside an empty field. Enter still works for anyone who
  // reaches for it, and the effect covers a paste.
  useEffect(() => {
    if (POSTAL_CODE.test(postal)) onPostal(postal);
    // `onPostal` is recreated per render by the caller; depending on it would
    // re-fire the lookup on every keystroke elsewhere on the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postal]);

  // "All" carries NO count. The type facets are computed over the unfiltered
  // tag set (by design — the chips must always offer every available type), so
  // an aggregate here would read "All 836" directly above a summary line
  // saying "3 clinics" whenever an area or a name filter is on.
  const chips: { key: string; label: string; count: number | null }[] = [
    { key: "all", label: "All", count: null },
    ...facets.map((f) => ({
      key: `${f.country}:${f.clinic_type}`,
      label: f.label,
      count: f.count as number | null,
    })),
  ];

  return (
    <Mount className="gap-0 p-2.5 sm:p-3">
      <div className="flex flex-col gap-2 py-1.5 sm:flex-row sm:items-center sm:gap-3.5">
        <span className="leaf-label sm:w-32 sm:shrink-0 sm:whitespace-nowrap">Where you are</span>
        {origin ? (
          <span className="inline-flex max-w-full items-center gap-2 self-start rounded-pill bg-shade py-1 pl-3.5 pr-1 text-row">
            <LocateFixed className="size-4 shrink-0 text-action-ink" aria-hidden />
            <span className="min-w-0 truncate">
              {origin.postal && <span className="font-semibold">{origin.postal}</span>}
              {origin.postal && origin.label && <span className="text-label"> · </span>}
              {origin.label && <span className="text-label">{origin.label}</span>}
            </span>
            <button
              type="button"
              onClick={() => {
                setPostal("");
                onClearOrigin();
              }}
              aria-label="Clear location"
              className="leaf-focus grid size-8 shrink-0 place-items-center rounded-pill text-label hover:bg-bar/70 hover:text-record"
            >
              <X className="size-4" aria-hidden />
            </button>
          </span>
        ) : (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-2.5">
            <button
              type="button"
              onClick={onGps}
              disabled={busy}
              className={cn(locateClass, "disabled:opacity-60")}
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <LocateFixed className="size-4" aria-hidden />
              )}
              Use my location
            </button>
            <span className="hidden text-row text-label sm:inline">or</span>
            <label htmlFor={postalId} className="sr-only">
              Your postal code
            </label>
            <input
              id={postalId}
              value={postal}
              onChange={(e) => setPostal(e.target.value.replace(/\D/g, "").slice(0, 6))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && POSTAL_CODE.test(postal)) onPostal(postal);
              }}
              inputMode="numeric"
              autoComplete="postal-code"
              placeholder="Postal code"
              disabled={busy}
              className={cn(leafControl, "sm:w-36")}
            />
          </div>
        )}
      </div>

      {status.kind === "error" && (
        // Announced: it arrives after a tap, and the member is looking at the
        // control they just pressed.
        <p role="alert" className="pb-1.5 text-row text-strike-pending">
          {status.message}
        </p>
      )}

      <MountRule />

      <div className="flex flex-col gap-2 py-1.5 sm:flex-row sm:items-center sm:gap-3.5">
        <span className="leaf-label sm:w-32 sm:shrink-0 sm:whitespace-nowrap">What to show</span>
        <div
          className="flex min-w-0 flex-1 flex-wrap gap-1"
          role="group"
          aria-label="Clinic type"
        >
          {chips.map((chip) => {
            const active = typeKey === chip.key;
            return (
              <button
                key={chip.key}
                type="button"
                onClick={() => onTypeKey(chip.key)}
                aria-pressed={active}
                className={cn(
                  "leaf-focus inline-flex min-h-11 items-center gap-1.5 rounded-pill px-3.5 text-row",
                  "transition-colors duration-200 ease-leaf",
                  // Selection is marked the way the nav, dock and tabs mark it:
                  // ink on shade, never the action colour — these PICK a view.
                  active
                    ? "bg-shade font-semibold text-record"
                    : "text-label hover:bg-shade/60 hover:text-record",
                )}
              >
                {chip.label}
                {chip.count !== null && (
                  <span className="font-normal text-label">{chip.count}</span>
                )}
              </button>
            );
          })}
        </div>
        <div className="flex gap-2 sm:shrink-0">
          <div className="relative min-w-0 flex-1 sm:flex-none">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-label"
              aria-hidden
            />
            <label htmlFor={searchId} className="sr-only">
              Filter by clinic name, address or postal code
            </label>
            <input
              id={searchId}
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              maxLength={MAX_QUERY_LENGTH}
              placeholder="Name or address"
              className={cn(leafControl, "pl-9 sm:w-54")}
            />
          </div>
          {areas.length > 0 && (
            <>
              <label htmlFor={areaId} className="sr-only">
                Filter by area
              </label>
              <select
                id={areaId}
                value={area}
                onChange={(e) => onArea(e.target.value)}
                className={cn(leafControl, "w-32 shrink-0 sm:w-37")}
              >
                <option value={ALL_AREAS}>All areas</option>
                {areas.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      </div>
    </Mount>
  );
}
