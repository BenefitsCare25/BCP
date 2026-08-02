/** One clinic, as a ledger row.
 *
 * The page it sits on is opened by someone standing somewhere, so the row is
 * built around the two decisions they are making: *is this the one*, and *what
 * do I do about it*. Everything else waits behind the disclosure.
 *
 * The five-column grid and the reasoning behind its two layouts live in
 * `leaf.css` (`.leaf-clinic-row`) — quoted `grid-template-areas` do not
 * survive a Tailwind class name, and the two layouts read better side by side
 * than split across a dozen responsive utilities.
 *
 * **The disclosure is a stretched button**, so the whole head opens the hours
 * while the clinic name stays a real `h3` OUTSIDE it — a `<button>` may not
 * legally contain a heading. Call and Directions therefore need `relative
 * z-10`, or the overlay swallows the tap; the same caveat `leaf/Action.tsx`
 * carries for stretched links. */
import { useId, useState } from "react";
import { ChevronDown, Map, Phone } from "lucide-react";
import type { Clinic } from "@/api/panelListings";
import { actionClass } from "@/components/portal/leaf/Action";
import { MountRule } from "@/components/portal/leaf/Mount";
import { cn } from "@/lib/cn";
import {
  openStateFor,
  stripDayPrefix,
  type HoursKey,
  type OpenState,
} from "@/lib/clinicHours";
import { readableAddress, readableCase, splitPhone } from "@/lib/clinicText";

const HOURS_ROWS: [HoursKey, string][] = [
  ["mon_fri", "Mon–Fri"],
  ["sat", "Sat"],
  ["sun", "Sun"],
  ["public_holiday", "Public holiday"],
];

/** 44px on touch, compact from `sm` up — see the sizing note in leaf.css. */
const callClass = actionClass("quiet", { className: "px-3.5 sm:h-9 sm:px-3" });

/** The "See all limits →" tier rather than a second pill: two coloured pills a
 * row, ten rows deep, is the wall this rewrite removes. */
const directionsClass =
  "leaf-focus group inline-flex min-h-11 items-center gap-1.5 rounded-pill px-2 " +
  "text-row font-semibold text-action-ink sm:min-h-8";

function StateMark({ state }: { state: OpenState }) {
  return (
    <span
      className={cn(
        "relative whitespace-nowrap pb-[3px] text-2xs font-bold uppercase leading-4 tracking-[0.085em]",
        "after:absolute after:inset-x-0 after:bottom-0 after:h-[2px] after:bg-current after:content-['']",
        // Struck in its own ink, never tinted text on a tinted wash. "Closed"
        // takes the label ink rather than a warning colour: a clinic being shut
        // at 3pm is a fact about the clock, not a fault.
        state.tone === "open" ? "text-strike-approved" : "text-label",
      )}
    >
      {state.label}
    </span>
  );
}

export function ClinicRow({
  clinic,
  clock,
}: {
  clinic: Clinic;
  /** Read once for the whole list, so every row agrees about "now". */
  clock: { key: HoursKey; minutes: number };
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  const hours = clinic.hours ?? {};
  const name = readableCase(clinic.name);
  const state = openStateFor(clinic.hours, clock);
  const phone = splitPhone(clinic.phone);
  // `specialty` is empty on every GP panel and is the whole point of a
  // SPECIALIST one — it says what the clinic treats, so a member browsing SP
  // listings has nothing to tell them apart without it.
  const gloss = [
    clinic.type_label,
    readableCase(clinic.area),
    readableCase(clinic.specialty),
    readableCase(clinic.doctor),
  ]
    .filter(Boolean)
    .join(" · ");
  const hasHours = HOURS_ROWS.some(([key]) => hours[key]);

  return (
    <li>
      <div
        className={cn(
          "leaf-clinic-row relative rounded-control px-3 py-2.5",
          "transition-colors duration-200 ease-leaf hover:bg-shade/50",
          open && "bg-shade/50",
        )}
      >
        <div data-cell="main" className="min-w-0">
          <h3 className="text-md font-semibold leading-5 text-record">{name}</h3>
          {gloss && <p className="truncate text-row leading-5 text-label">{gloss}</p>}
          {clinic.address && (
            <p className="truncate text-row leading-5 text-label">
              {readableAddress(clinic.address)}
            </p>
          )}
        </div>

        {clinic.distance_km !== null && (
          <span
            data-cell="dist"
            className="whitespace-nowrap text-row font-semibold text-record"
          >
            {formatDistance(clinic.distance_km)}
          </span>
        )}

        {state && (
          <>
            <span data-cell="state">
              <StateMark state={state} />
            </span>
            {state.detail && (
              <span data-cell="sub" className="whitespace-nowrap text-2xs text-label">
                {state.detail}
              </span>
            )}
          </>
        )}

        {/* `relative z-10` — these sit under the disclosure's stretched overlay. */}
        {phone.dial && (
          <a
            data-cell="call"
            href={`tel:${phone.dial}`}
            aria-label={`Call ${name} on ${phone.display}`}
            className={cn(callClass, "relative z-10")}
          >
            <Phone className="size-4 shrink-0" aria-hidden />
            {phone.display}
          </a>
        )}
        {clinic.google_map_url && (
          <a
            data-cell="dirs"
            href={clinic.google_map_url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(directionsClass, "relative z-10")}
          >
            <Map className="size-4 shrink-0" aria-hidden />
            Directions
            <span
              aria-hidden
              className="transition-transform duration-200 ease-leaf group-hover:translate-x-1"
            >
              →
            </span>
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        )}

        {hasHours && (
          <button
            data-cell="disc"
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            // Only while the panel exists. A reference to an element that is
            // not in the document tells a screen reader there is somewhere to
            // move to and then cannot deliver it — the same rule `leaf/Deck`
            // follows for its unselected tabs.
            aria-controls={open ? panelId : undefined}
            aria-label={`Opening hours for ${name}`}
            className={cn(
              "leaf-focus grid size-7 place-items-center rounded-pill text-label",
              "after:absolute after:inset-0 after:rounded-control after:content-['']",
            )}
          >
            <ChevronDown
              className={cn(
                "size-4.5 transition-transform duration-200 ease-leaf",
                open && "rotate-180",
              )}
              aria-hidden
            />
          </button>
        )}
      </div>

      {open && hasHours && (
        <div id={panelId} className="px-3 pb-3 pt-0.5">
          <MountRule className="mb-1.5" />
          {/* Two columns, but a wide gap and a wrapping value: a clinic that
              lists a per-day exception runs to ~40 characters, and at a tight
              gap it butts against the term in the next column and the reading
              order stops being obvious. */}
          <dl className="grid grid-cols-1 gap-x-12 sm:grid-cols-2">
            {HOURS_ROWS.map(([key, label]) => {
              const value = hours[key];
              if (!value) return null;
              const isToday = key === clock.key;
              return (
                <div key={key} className="flex items-baseline justify-between gap-3 py-1">
                  <dt
                    className={cn(
                      "shrink-0 text-row",
                      isToday ? "font-semibold text-record" : "text-label",
                    )}
                  >
                    {label}
                  </dt>
                  <dd
                    className={cn(
                      "min-w-0 text-right text-row",
                      isToday ? "font-semibold text-record" : "text-label",
                    )}
                  >
                    {stripDayPrefix(readableCase(value))}
                  </dd>
                </div>
              );
            })}
          </dl>
          {/* The half of the phone cell that is about the visit rather than
              about the panel's paperwork — see `splitPhone`. */}
          {phone.note && <p className="mt-2 text-row text-label">{phone.note}</p>}
        </div>
      )}
    </li>
  );
}

export function formatDistance(km: number): string {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}
