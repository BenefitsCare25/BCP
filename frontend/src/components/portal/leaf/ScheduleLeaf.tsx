/** The schedule of benefits, set in the leaf's language.
 *
 * Presentation only — every decision about *what* a schedule says (which rows
 * carry content, which are reference material, how a value formats, which rows
 * earn the headline) comes from `lib/benefitSchedule`, shared with the broker's
 * renderer so the two surfaces can never disagree.
 *
 * **No claim figures here.** This is the entitlement — what the policy covers.
 * What has been claimed against it, what is still under review and what is left
 * are a different question with its own tab ("What's left"), and mixing the two
 * made a schedule row state a limit and a balance in the same breath. The
 * shared reader still accepts a usage map, because the broker's renderers do
 * show both; this surface simply does not pass one. */
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import type {
  BenefitItem,
  BenefitLimit,
  BenefitSchedule,
  BenefitSubItem,
} from "@/types";
import { propertyLabel } from "@/lib/sob";
import {
  displayProps,
  formatValue,
  isEnumeration,
  readSchedule,
  subItemsOf,
} from "@/lib/benefitSchedule";
import { MountRule } from "./Mount";

/** Schedules carry Singapore dollars; `formatValue` defaults to a bare "$" for
 * the broker app, which is not what this surface writes. */
const MEMBER_CURRENCY = "S$";

function LimitNotes({ limits }: { limits?: BenefitLimit[] }) {
  if (!limits || limits.length === 0) return null;
  return (
    <p className="mt-0.5 text-row text-label">
      {limits
        .map((l) => (l.value ? `${l.label}: ${l.value}` : l.label))
        .join(" · ")}
    </p>
  );
}

function ScheduleRow({
  label,
  value,
  kind,
  note,
  limits,
  indent,
}: {
  label: string;
  value?: string | null;
  kind?: BenefitItem["kind"];
  note?: string | null;
  limits?: BenefitLimit[];
  indent?: boolean;
}) {
  // The member surface writes money as S$ everywhere else.
  const formatted = formatValue(value, kind, MEMBER_CURRENCY);
  // A value that reads as a sentence rather than an amount goes full width
  // below its label; squeezing it into the right-hand column forces the label
  // to wrap one word per line.
  const longForm = formatted != null && formatted.length > 40;

  return (
    <div className={indent ? "py-1.5 pl-4" : "py-1.5"}>
      <div
        className={
          longForm
            ? "flex flex-col gap-0.5"
            : "flex items-baseline justify-between gap-4"
        }
      >
        <dt
          className={`min-w-0 break-words text-row ${
            indent ? "text-label" : "text-record"
          }`}
        >
          {label}
        </dt>
        {formatted && (
          <dd
            className={`text-row ${
              longForm ? "text-label" : "shrink-0 text-right font-medium text-record"
            }`}
          >
            {formatted}
          </dd>
        )}
      </div>
      {note && (
        <dd className="mt-0.5 text-row text-label">{note}</dd>
      )}
      <dd>
        <LimitNotes limits={limits} />
      </dd>
    </div>
  );
}

/** A covered-conditions or compensation-scale list: reference material, not a
 * limit, so it collapses to one line rather than burying the rows that are. */
function Enumeration({
  item,
  hidden = false,
}: {
  item: BenefitItem;
  hidden?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const subs = subItemsOf(item);
  const label = item.name ?? "";

  return (
    <div className="py-1.5" hidden={hidden}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="leaf-focus flex w-full min-h-11 items-center justify-between gap-3 text-left"
      >
        <span className="min-w-0 break-words text-row text-record">
          {label}
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-row text-label">
          {subs.length} listed
          <ChevronDown
            className={`size-4 transition-transform ${open ? "rotate-180" : ""}`}
            aria-hidden
          />
        </span>
      </button>
      {item.note && (
        <p className="text-row text-label">{item.note}</p>
      )}
      {open &&
        subs.map((sub: BenefitSubItem, i) => (
          <ScheduleRow
            key={i}
            indent
            label={`${sub.key ? `${sub.key} ` : ""}${sub.name}`}
            value={sub.value}
            kind={sub.kind}
            note={sub.note}
            limits={sub.limits}
          />
        ))}
    </div>
  );
}

function Item({
  item,
  hidden = false,
}: {
  item: BenefitItem;
  /** Outside the headline while the schedule is collapsed. `display:none`, so
   * the row keeps its position in document order without being announced or
   * tabbable — see the note at the call site. */
  hidden?: boolean;
}) {
  if (isEnumeration(item)) return <Enumeration item={item} hidden={hidden} />;

  // The slip's row number is deliberately dropped on the member surface. It is
  // a position in the insurer's document, not a fact about the benefit, and the
  // parser emits values like "-1" and "1A" that read as broken to someone who
  // has never seen the slip. Brokers keep the numbers for reconciliation.
  const label = item.name ?? "";
  const subs = subItemsOf(item).filter(
    (s) => s.value || s.note || (s.limits && s.limits.length > 0),
  );

  return (
    <div hidden={hidden}>
      <ScheduleRow
        label={label}
        value={item.value}
        kind={item.kind}
        note={item.note}
        limits={item.limits}
      />
      {displayProps(item.properties).map(([key, value]) => (
        <ScheduleRow key={key} indent label={propertyLabel(key)} value={value} />
      ))}
      {subs.map((sub, i) => (
        <ScheduleRow
          key={i}
          indent
          label={`${sub.key ? `${sub.key} ` : ""}${sub.name}`}
          value={sub.value}
          kind={sub.kind}
          note={sub.note}
          limits={sub.limits}
        />
      ))}
    </div>
  );
}

export function ScheduleLeaf({
  schedule,
  annualPolicyLimit,
  titleId,
}: {
  schedule: BenefitSchedule | null | undefined;
  annualPolicyLimit?: string | null;
  /** Ties the disclosure's label to the mount it belongs to. */
  titleId?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  const { items, headline, collapsible, valuesMissing } = readSchedule(
    schedule?.items,
  );

  if (items.length === 0) {
    return (
      <p className="text-row text-label">
        The detailed benefit list for this plan isn't available yet.
      </p>
    );
  }

  // **One list, in the insurer's own row order.** The schedule is a legal
  // document and a member reads it against the copy their HR team sent; the
  // broker's renderer (`BenefitScheduleView`) shows true document order, so a
  // reordered member view means the two surfaces present the same schedule
  // differently.
  //
  // It used to be two lists — headline, then the tail beneath it — which reads
  // 1, 3, 4, 5, 6, 12, then 2, 7, 8… once opened, because a row earns the
  // headline by carrying a value or a claim, not by its position. The aperture
  // is now a per-row disclosure over ONE ordered list instead. That also costs
  // the panel's height animation, which is no loss to account for: DESIGN.md
  // spends motion on four things and a schedule disclosure is not among them.
  const hiddenSet = collapsible
    ? new Set(items.filter((i) => !headline.includes(i)))
    : new Set<(typeof items)[number]>();
  const panelId = titleId ? `${titleId}-schedule-tail` : undefined;

  return (
    <div>
      {/* The slip's cover description is NOT rendered here. It moved up to sit
          directly under the product's title (`BenefitMount`), where it replaces
          the generic per-code gloss instead of repeating it further down. */}
      {annualPolicyLimit && (
        <p className="mb-2 text-row text-record">
          <span className="text-label">Yearly cap · </span>
          {formatValue(annualPolicyLimit, undefined, MEMBER_CURRENCY) ??
            annualPolicyLimit}
        </p>
      )}
      {valuesMissing && (
        <p className="mb-2 text-row text-label">
          These are the benefits you're covered for. The amounts for this plan
          aren't recorded yet — your HR team can confirm them.
        </p>
      )}

      {/* Every row is mounted, in order. A row outside the headline is `hidden`
          while collapsed — `display:none`, so it is neither announced nor
          tabbable — rather than unmounted, which keeps `panelId` resolvable for
          the `aria-controls` on the button below even in the collapsed state,
          i.e. exactly when that association is what tells a screen-reader user
          there is something to open. */}
      <dl id={panelId} className="divide-y divide-hairline/75">
        {items.map((item, idx) => (
          <Item
            key={`${item.number}-${idx}`}
            item={item}
            hidden={!showAll && hiddenSet.has(item)}
          />
        ))}
      </dl>

      {/* The aperture. The frame grows outward to admit the rest of the
          schedule rather than a new page replacing it — the world's own
          gesture. An in-place disclosure rather than a dialog, because this is
          a reading task that needs neither interruption nor protected focus:
          no focus trap, no scroll lock, and `aria-expanded` + `aria-controls`
          carry the state that a dialog would have carried structurally. */}
      {collapsible && (
        <>
          <MountRule className="mt-1" />
          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            aria-expanded={showAll}
            aria-controls={panelId}
            className="leaf-focus mt-1 flex min-h-11 w-full items-center justify-between gap-2 text-left"
          >
            {/* **ONE count, not two.** This read "Show all 10 benefits" on the
                left and "4 more" on the right — both true (ten rows in the
                schedule, four of them hidden) and, side by side on one control,
                simply confusing: two numbers on one button describe one
                quantity, so the reader tries to reconcile them.
                The schedule's own size is the one that survives, because that
                is what the button reveals and what the insurer's document
                states. The tail count is derivable and was never the point.

                Ink, not brand. A fully covered member holds nine of these
                mounts, so a brand-coloured disclosure would put the brand on
                the screen nine times — the third appearance is already one too
                many (The Twice Rule). */}
            <span className="text-row font-semibold text-record">
              {showAll ? "Show fewer" : `Show all ${items.length} benefits`}
            </span>
            <ChevronDown
              className={`size-4 shrink-0 text-label transition-transform duration-200 ease-leaf ${
                showAll ? "rotate-180" : ""
              }`}
              aria-hidden
            />
          </button>
        </>
      )}
    </div>
  );
}
