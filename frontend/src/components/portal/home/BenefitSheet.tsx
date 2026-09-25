import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import type { Utilization } from "@/types";
import { cn } from "@/lib/cn";
import { benefitHeadline } from "../leaf/benefitHeadline";
import type { CareRoute } from "../leaf/careRoutes";
import { careArt, careTone } from "../leaf/careTone";
import { productShortLabel } from "../leaf/glossary";
import { useCompany } from "../useCompany";

/** A card has room for one short phrase, not the schedule's full wording.
 *  "Covered as charged · S$5 co-pay per visit" → "Covered as charged" + detail;
 *  "1-bed ward, restructured hospital" → "1-bed ward". The clause carrying
 *  an amount wins, else the first — and what it leaves out is NOT dropped:
 *  it rides on the line beneath ("Covered as charged"). Dropping it made the
 *  GP card read the co-payment as if S$5 were the whole benefit, when
 *  the slip says the visit is covered as charged less a S$5 co-payment. */
export function conciseFact(value: string): { lead: string; rest: string | null } {
  const parts = value.split(/\s+[·—–]\s+|,\s+/).map((part) => part.trim()).filter(Boolean);
  const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
  // What the member PAYS is never the headline of a benefit — leading with
  // "You pay S$5 a visit" read as if S$5 were the cover. The cover leads and
  // the co-payment sits beneath it: "Covered as charged" / "S$5 co-payment a visit".
  const payPart = parts.find((part) => /\bco-pay\b/i.test(part));
  if (payPart && parts.length > 1) {
    const cover = parts.filter((part) => part !== payPart).join(", ");
    return { lead: cap(cover), rest: payPart };
  }
  const pick = parts.find((part) => /\d/.test(part)) ?? parts[0] ?? value;
  const rest = parts.filter((part) => part !== pick).join(", ");
  return { lead: cap(pick), rest: rest ? cap(rest) : null };
}

/** "Outpatient Kidney Dialysis / Cancer Treatment: S$20,000 left" →
 *  "Kidney dialysis & cancer treatment: S$20,000 left". */
function conciseNote(note: string): string {
  const tidy = note.replace(/^Outpatient\s+/i, "").replace(/\s*\/\s*/g, " & ");
  const lower = tidy.charAt(0) + tidy.slice(1).replace(/\b([A-Z])([a-z]+)/g, (_, a: string, b: string) => a.toLowerCase() + b);
  // The amount leads, so a one-line clamp can only ever cut the words, never
  // the number: "S$20,000 left for kidney dialysis & cancer treatment".
  const split = lower.match(/^(.+?):\s*(.+\bleft)$/);
  return split ? `${split[2]} for ${split[1].charAt(0).toLowerCase()}${split[1].slice(1)}` : lower;
}

/** The inside of a benefit sheet: tag, title, the one figure that matters and
 *  its clay object. Wrapped by a link on Home and a button on Coverage, so the
 *  whole sheet is always ONE target with no nested controls. */
function SheetContent({ route, utilization, go }: { route: CareRoute; utilization?: Utilization; go: string }) {
  const headline = benefitHeadline(route.key, route.lines, utilization);
  const art = careArt(route.key);
  const tag = productShortLabel(route.lines[0].product_code, route.lines[0].product_name);
  const fact = headline ? conciseFact(headline.value) : null;
  // One detail line: a tracked sub-limit outranks the rest of the wording.
  const detail = headline?.note ? conciseNote(headline.note) : fact?.rest ?? null;
  // Every child below is a ROW of the card's subgrid, rendered even when
  // empty, so label / figure / detail line up across a whole row of cards.
  return (
    <>
      <span className="clay-tag">{tag}</span>
      {art && <img className="clay-sheet-art" src={art} alt="" loading="lazy" />}
      <span className="clay-sheet-title">{route.title}</span>
      <span className="clay-sheet-label">{headline?.label ?? ""}</span>
      <span className={cn("clay-sheet-value", !fact && "is-quiet", fact && fact.lead.length > 14 && "is-words")} title={headline?.value}>
        {fact ? fact.lead : route.description}
      </span>
      <span className="clay-sheet-detail" title={headline?.note ?? undefined}>{detail ?? ""}</span>
      {headline?.used !== undefined ? (
        <span className="clay-meter" role="presentation">
          <span style={{ width: `${Math.round(headline.used * 100)}%` }} />
        </span>
      ) : <span aria-hidden />}
      <span className="clay-sheet-go">
        {go} <ArrowRight className="size-4" aria-hidden />
      </span>
    </>
  );
}

const sheetClass = (route: CareRoute) => cn("clay-sheet leaf-focus text-left", `tone-${careTone(route.key)}`);

export function BenefitSheet({
  route,
  utilization,
  to,
}: {
  route: CareRoute;
  utilization: Utilization | undefined;
  to: { tab: string; p: string };
}) {
  const company = useCompany();
  return (
    <Link to="/portal/$company/coverage" params={{ company }} search={to} className={sheetClass(route)}>
      <SheetContent route={route} utilization={utilization} go="See cover" />
    </Link>
  );
}

export function BenefitSheetButton({ route, onClick }: { route: CareRoute; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={sheetClass(route)}>
      <SheetContent route={route} go="View cover" />
    </button>
  );
}
