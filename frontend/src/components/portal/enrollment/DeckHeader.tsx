/** The two facts that govern every slide of the enrollment deck: how long the
 * member has, and what they have left to spend.
 *
 * Neither belongs to a product, so neither can live on a slide — a deck shows
 * one slide at a time, and a budget you can only see by opening the right one
 * is not a budget you can spend against. Both therefore ride furniture that
 * stays on screen, and WHICH furniture depends on the width:
 *
 * - **The deadline goes in the deck's sticky rail at every width.** It is the
 *   reason the page exists today rather than next month, and the rail is the
 *   one part of a deck that survives scrolling a sixty-nine-row schedule.
 * - **The balance goes in the page's heading row from `lg` up**, beside the
 *   member's name and the benefit year — the row that already carries what
 *   scopes the page. Below `lg` that row does not exist (on a phone the top bar
 *   carries the name), so the balance joins the deadline in the rail instead.
 *
 * **One instance, never two.** The caller picks the placement from
 * `useHeadRailWidth()` — the same signal `HeadRail` itself gates on — rather
 * than rendering both and hiding one with a media query. Two live copies of a
 * figure that moves as the member chooses is two chances to state a different
 * number. */
import type { FlexSummary } from "@/components/enrollment/electionCore";
import { flexShort } from "@/components/enrollment/electionCore";
import { Money } from "@/components/portal/leaf/Figure";
import { formatDay } from "@/components/portal/leaf/date";
import { cn } from "@/lib/cn";

/** A printed term with its figure, on one baseline. The rail is 236px wide at
 * its widest, so the term is the uppercase furniture tier and the value carries
 * the weight. */
function MeterRow({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="leaf-label">{term}</span>
      <span className="text-row font-semibold text-record">{children}</span>
    </div>
  );
}

function balanceTerm(flex: FlexSummary): string {
  return flexShort(flex) ? "Short by" : "Left to spend";
}

function BalanceFigure({ flex }: { flex: FlexSummary }) {
  const short = flexShort(flex);
  return (
    <Money
      value={Math.abs(flex.balance)}
      currency={flex.currency ?? "S$"}
      emphasis="strong"
      className={short ? "text-strike-pending" : undefined}
    />
  );
}

/** Pinned above the deck's index, inside its sticky container. */
export function RailHeader({
  closesAt,
  flex,
}: {
  /** Null once the enrollment is finalized — there is no longer a deadline to
   *  act by, and the status note above the deck says so. */
  closesAt: string | null;
  /** Null when the balance is in the heading row instead, or when the member has
   *  no flexible-benefits wallet at all. */
  flex: FlexSummary | null;
}) {
  if (!closesAt && !flex) return null;
  return (
    <div className="flex flex-col gap-1">
      {closesAt && <MeterRow term="Open till">{formatDay(closesAt)}</MeterRow>}
      {flex && (
        <MeterRow term={balanceTerm(flex)}>
          <BalanceFigure flex={flex} />
        </MeterRow>
      )}
    </div>
  );
}

/** The balance as a chip for the heading row, built like `BenefitYearControl`'s
 * static variant — same height, same radius, same hairline.
 *
 * The hairline is not decoration: on the near-white ground a fill alone gives
 * the chip no edge, and a figure with no edge between a 2xl name and a bordered
 * year control reads as stray text that landed in the gap. */
export function HeadBalance({ flex }: { flex: FlexSummary }) {
  return (
    <span
      className={cn(
        "inline-flex h-10 shrink-0 items-center gap-2 rounded-control px-3",
        "border border-hairline bg-shade text-row",
      )}
    >
      <span className="leaf-label">{balanceTerm(flex)}</span>
      <BalanceFigure flex={flex} />
    </span>
  );
}
