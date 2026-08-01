/** "What am I covered for" — the member's leaf.
 *
 * Replaces `BenefitStatement` on member surfaces. That component is the
 * broker's placement-slip renderer pointed at a member token: the server strips
 * the figures it must, but the copy, hierarchy, vocabulary and density were
 * never re-authored. This is the re-authoring.
 *
 * Roster attributes (job grade, salary band, entity) are deliberately not
 * rendered. They are how the company files the member, not an answer to any of
 * the four questions a member actually opens this page with.
 *
 * **This tab is ENTITLEMENT, not account.** No claimed totals, no "still under
 * review", no remaining balances on an insured product — that is the "What's
 * left" tab's question, and interleaving the two made a page about what the
 * policy covers read as a statement of account. Flexible benefits is the single
 * exception and it is not really one: a flex wallet's allowance and what remains
 * of it ARE its entitlement, so `FlexMount` keeps its ledger. Consequently
 * nothing here reads `Utilization` at all.
 *
 * **The products are a DECK, not a stack** — see `Deck.tsx` for why. This module
 * owns what belongs to coverage rather than to the deck: which slides exist,
 * what each is called in the rail, and how the selection is carried (the member
 * page hands it a URL parameter; the broker's employee-view preview lets the
 * deck hold its own). */
import { useMemo } from "react";
import type { BenefitStatement } from "@/types";
import { BenefitMount } from "./BenefitMount";
import { FlexMount } from "./FlexMount";
import { Mount } from "./Mount";
import { Deck, type DeckSlide } from "./Deck";
import { productShortLabel } from "./glossary";

/** The flex wallet's slide key. Namespaced so it can never collide with a
 * product code, since both share one deck and one `?p=` parameter. */
export const FLEX_SLIDE_KEY = "flex";

export function CoverageLeaf({
  data,
  productKey,
  onProductKeyChange,
}: {
  data: BenefitStatement;
  /** Controlled selection. Omit entirely for a self-driving deck. */
  productKey?: string | null;
  onProductKeyChange?: (key: string) => void;
}) {
  const hasFlex = Boolean(data.flex);
  // Gate on what there is to RENDER, not on `is_matched`. A member can be
  // matched and still have no coverage lines — `hydrate_plans` skips
  // matched_categories entries whose category was deleted or re-parsed — and
  // gating on the flag alone rendered an empty page with no explanation.
  const hasAnyCoverage = data.coverage.length > 0 || hasFlex;

  const slides = useMemo<DeckSlide[]>(() => {
    const raw = data.coverage.map((line) => ({
      code: line.product_code,
      label: productShortLabel(line.product_code, line.product_name),
      // The disambiguators, in the order they are tried below.
      plan: line.plan_code,
      name: line.product_name,
      render: (rise: boolean) => <BenefitMount line={line} rise={rise} />,
    }));

    if (data.flex) {
      raw.push({
        code: FLEX_SLIDE_KEY,
        label: "Flexible benefits",
        plan: null,
        name: null,
        render: (rise: boolean) => <FlexMount flex={data.flex!} rise={rise} />,
      });
    }

    // **A product code is not guaranteed unique across a statement.**
    // `hydrate_plans` emits a line per matched CATEGORY and the product index is
    // keyed on id, not code; a firm-library product and a company one may carry
    // the same code (the unique constraint exempts `client_id IS NULL`), and an
    // unlinked category falls back to "?". Two lines sharing a code collided on
    // the React key, on the `deck-tab-`/`deck-panel-` ids that wire the tablist
    // together, and on the `findIndex` that resolves a selection — so the second
    // line was simply unreachable. The stack it replaced at least rendered both.
    const keyCount = new Map<string, number>();
    // Two codes can also share a rail LABEL: `GHS`/`GHS2` and `GMM`/`GMM2` exist
    // precisely so one slip can carry two hospital or two major-medical plans,
    // and both map to the same short form. Two chips reading "Hospital" name
    // nothing. Disambiguated by the plan code the member's own mount is titled
    // with, else the insurer's product name.
    const labelCount = new Map<string, number>();
    for (const s of raw) labelCount.set(s.label, (labelCount.get(s.label) ?? 0) + 1);

    return raw.map((s) => {
      const n = (keyCount.get(s.code) ?? 0) + 1;
      keyCount.set(s.code, n);
      const ambiguous = (labelCount.get(s.label) ?? 0) > 1;
      return {
        key: n === 1 ? s.code : `${s.code}~${n}`,
        label: ambiguous
          ? s.plan
            ? `${s.label} · Plan ${s.plan}`
            : (s.name ?? `${s.label} ${n}`)
          : s.label,
        render: () => s.render(false),
      };
    });
  }, [data.coverage, data.flex]);

  // The lone-benefit path renders the mount OUTSIDE the deck, where nothing else
  // owns its arrival — so it keeps the entrance every other mount in the portal
  // gets. `rise={false}` is a statement about the deck, not about the mount.
  const soloRender = useMemo(() => {
    if (data.coverage.length === 1 && !data.flex) {
      const line = data.coverage[0];
      return () => <BenefitMount line={line} />;
    }
    if (data.coverage.length === 0 && data.flex) {
      const flex = data.flex;
      return () => <FlexMount flex={flex} />;
    }
    return null;
  }, [data.coverage, data.flex]);

  if (!hasAnyCoverage) {
    return (
      <Mount label="No benefits on record">
        <p className="text-row text-label">
          We don't have any benefits recorded against your name for this
          period. If you think that's wrong, your HR team can check your record.
        </p>
      </Mount>
    );
  }

  // One benefit is not a set, and a rail naming its only member — beside a
  // counter reading "1 of 1" and two dead arrows — is chrome describing
  // nothing. Show the mount.
  if (slides.length < 2) {
    return <>{soloRender ? soloRender() : slides[0].render()}</>;
  }

  // **Opens on the FIRST product, in the statement's own order.** It used to
  // open on whichever product had a claim in flight, which was defensible while
  // this tab showed claim figures — the member could see why they had landed
  // there. Now that it shows entitlement only, that reason is invisible, and a
  // page that opens halfway down a list for an unstated reason is the same
  // failure as the activity dot this rail used to carry.
  return (
    <Deck
      slides={slides}
      label="Your benefits"
      activeKey={productKey}
      onActiveKeyChange={onProductKeyChange}
    />
  );
}
