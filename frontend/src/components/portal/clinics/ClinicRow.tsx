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
 * carries for stretched links.
 *
 * **The row is memoised and its readings are cached on the clinic.** Every
 * value on screen is derived — the hours are parsed with a regex scan, four
 * fields are re-cased, the phone cell is split — and the list renders up to two
 * hundred of these while the name filter updates on each keystroke. The parent
 * passes no callbacks, so `memo` is not defeated by a fresh arrow per render;
 * if one is ever added it has to be stable, the same rule the SOB table's rows
 * live by. */
import { memo, useId, useMemo, useState } from "react";
import { ChevronDown, Map, Phone } from "lucide-react";
import type { Clinic } from "@/api/panelListings";
import { actionClass } from "@/components/portal/leaf/Action";
import { MountRule } from "@/components/portal/leaf/Mount";
import { cn } from "@/lib/cn";
import {
  daysNamedBy,
  openStateFor,
  stripDayPrefix,
  type ClinicClock,
  type DayName,
  type HoursKey,
  type OpenState,
} from "@/lib/clinicHours";
import {
  readableAddress,
  readableCase,
  splitPhone,
  type PhoneParts,
} from "@/lib/clinicText";

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

/** How to reach the clinic.
 *
 * **A cell with no dialable run still renders.** `splitPhone` answers empty for
 * "call the mall concierge", a WhatsApp handle or a pager, and rendering only
 * the `tel:` branch left those rows with no way to contact the clinic at all —
 * which is half of what the member came to the page for.
 *
 * `relative z-10` — this sits under the disclosure's stretched overlay. */
function ContactCell({
  name,
  phone,
  contact,
}: {
  name: string;
  phone: PhoneParts;
  /** The raw cell, already re-cased, when there is nothing to dial. */
  contact: string;
}) {
  if (phone.dial) {
    return (
      <a
        data-cell="call"
        href={`tel:${phone.dial}`}
        aria-label={`Call ${name} on ${phone.display}`}
        className={cn(callClass, "relative z-10")}
      >
        <Phone className="size-4 shrink-0" aria-hidden />
        {phone.display}
      </a>
    );
  }
  if (!contact) return null;
  return (
    <span data-cell="call" title={contact} className="max-w-52 truncate text-row text-label">
      {contact}
    </span>
  );
}

function DirectionsCell({ url }: { url: string }) {
  return (
    <a
      data-cell="dirs"
      href={url}
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
  );
}

function DisclosureCell({
  open,
  onToggle,
  panelId,
  label,
}: {
  open: boolean;
  onToggle: () => void;
  panelId: string;
  label: string;
}) {
  return (
    <button
      data-cell="disc"
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      // Only while the panel exists. A reference to an element that is not in
      // the document tells a screen reader there is somewhere to move to and
      // then cannot deliver it — the same rule `leaf/Deck` follows for its
      // unselected tabs.
      aria-controls={open ? panelId : undefined}
      aria-label={label}
      className={cn(
        "leaf-focus grid size-7 place-items-center rounded-pill text-label",
        "after:absolute after:inset-0 after:rounded-control after:content-['']",
      )}
    >
      <ChevronDown
        className={cn("size-4.5 transition-transform duration-200 ease-leaf", open && "rotate-180")}
        aria-hidden
      />
    </button>
  );
}

/** The days each cell is printed against, which is what decides whether its own
 * prefix is worth keeping. */
const CELL_DAYS: Record<HoursKey, DayName[]> = {
  mon_fri: ["mon", "tue", "wed", "thu", "fri"],
  sat: ["sat"],
  sun: ["sun"],
  public_holiday: ["ph"],
};

/** The value as the disclosure prints it, beside a term naming the cell.
 *
 * A prefix covering the WHOLE cell is dropped — the row already says "Mon–Fri"
 * and the cell would otherwise say it twice. A prefix naming a SUBSET is KEPT:
 * printing `9am - 1pm` against a Mon–Fri term for a clinic open Mon, Wed and
 * Fri states two more days of opening than the panel ever claimed. */
function statedHours(key: HoursKey, value: string | undefined): string {
  const text = readableCase(value);
  const named = daysNamedBy(value);
  const blanket = !named || CELL_DAYS[key].every((day) => named.has(day));
  return blanket ? stripDayPrefix(text) : text;
}

/** Behind the disclosure: the four stated lines, and the half of the phone cell
 * that is about the visit.
 *
 * **The note is not conditional on the hours.** It used to render inside a
 * panel that only existed when a line parsed, so "last registration is 30 mins
 * before closing" — the one remark that changes whether a member sets off —
 * was unreachable on exactly the rows whose hours nobody could read. */
function ClinicDetail({
  id,
  hours,
  clock,
  note,
}: {
  id: string;
  hours: Partial<Record<HoursKey, string | undefined>>;
  clock: ClinicClock;
  note: string | null;
}) {
  const rows = HOURS_ROWS.filter(([key]) => hours[key]);
  return (
    <div id={id} className="px-3 pb-3 pt-0.5">
      <MountRule className="mb-1.5" />
      {/* Two columns, but a wide gap and a wrapping value: a clinic that
          lists a per-day exception runs to ~40 characters, and at a tight
          gap it butts against the term in the next column and the reading
          order stops being obvious. */}
      {rows.length > 0 && (
        <dl className="grid grid-cols-1 gap-x-12 sm:grid-cols-2">
          {rows.map(([key, label]) => {
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
                  {statedHours(key, hours[key])}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
      {note && <p className={cn("text-row text-label", rows.length > 0 && "mt-2")}>{note}</p>}
    </div>
  );
}

export const ClinicRow = memo(function ClinicRow({
  clinic,
  clock,
}: {
  clinic: Clinic;
  /** Read once for the whole list, so every row agrees about "now". */
  clock: ClinicClock;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  const read = useMemo(() => {
    const hours = clinic.hours ?? {};
    return {
      hours,
      name: readableCase(clinic.name),
      address: readableAddress(clinic.address),
      phone: splitPhone(clinic.phone),
      // `specialty` is empty on every GP panel and is the whole point of a
      // SPECIALIST one — it says what the clinic treats, so a member browsing
      // SP listings has nothing to tell them apart without it.
      gloss: [
        clinic.type_label,
        readableCase(clinic.area),
        readableCase(clinic.specialty),
        readableCase(clinic.doctor),
      ]
        .filter(Boolean)
        .join(" · "),
      hasHours: HOURS_ROWS.some(([key]) => hours[key]),
    };
  }, [clinic]);

  const state = useMemo(() => openStateFor(clinic.hours, clock), [clinic.hours, clock]);

  const { name, phone } = read;
  // A cell carrying no dialable run is still the only contact the panel gave
  // us — "call the mall concierge", a WhatsApp handle. Dropping it left a row
  // with no way to reach the clinic at all.
  const contact = !phone.dial && clinic.phone ? readableCase(clinic.phone) : "";
  const hasDetail = read.hasHours || Boolean(phone.note);

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
          {read.gloss && <p className="truncate text-row leading-5 text-label">{read.gloss}</p>}
          {read.address && (
            <p className="truncate text-row leading-5 text-label">{read.address}</p>
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

        <ContactCell name={name} phone={phone} contact={contact} />
        {clinic.google_map_url && <DirectionsCell url={clinic.google_map_url} />}

        {hasDetail && (
          <DisclosureCell
            open={open}
            onToggle={() => setOpen((v) => !v)}
            panelId={panelId}
            label={read.hasHours ? `Opening hours for ${name}` : `More about ${name}`}
          />
        )}
      </div>

      {open && hasDetail && (
        <ClinicDetail id={panelId} hours={read.hours} clock={clock} note={phone.note} />
      )}
    </li>
  );
});

function formatDistance(km: number): string {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}
