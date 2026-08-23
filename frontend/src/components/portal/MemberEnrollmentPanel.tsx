/** "My enrollment" — the member's own election panel, rendered by the portal
 * page (interactive) and the broker's employee-view preview (`readOnly`).
 *
 * ## The products are a DECK, not a stack
 *
 * This page used to render one mount per product down a single column, below a
 * wallet and above two buttons. Measured on CDL's roster that is **9,300px —
 * thirteen screenfuls on a desktop and far worse on a phone** — because each
 * product carries a radio row per tier and each alternative tier carries the
 * schedule rows it differs on. A member with two real decisions to make had to
 * scroll past seven products to reach the second one, and the buttons that
 * commit the whole thing lived at the bottom of all of it.
 *
 * So it takes the shape the coverage tab already proved (`leaf/Deck`): a sticky
 * index naming every step, one step on stage. Three things follow from the fact
 * that this deck is a FORM and the coverage one is a document, and each of them
 * is load-bearing:
 *
 * **1. The index carries state.** A deck hides what it is not showing, which on
 * a form means a member can change their hospital plan, move on, and have no
 * way to see that they did. Every product the member has moved off the cover
 * they hold today is marked in the rail (`DeckSlide.mark`), so the index is a
 * record of the decisions taken rather than a list of names.
 *
 * **2. The deadline and the allowance are furniture, not content.** Every price
 * on every slide is spent against one budget, and every choice is bounded by one
 * date; neither can live on a slide a member has to go and find. The deadline
 * rides the deck's sticky rail and the running balance rides the page's heading
 * row (`DeckHeader`), so both are on screen wherever the member is. The wallet's
 * working — spent, traded, left — is on the review step, with the total it
 * explains.
 *
 * **3. It ends in a review.** See `ReviewMount`; Send carries the reviewed
 * choices as one atomic request rather than racing a separate draft save.
 *
 * Products the window gives the member no say over are folded into one slide
 * (`StandardMount`): given a mount each they were indistinguishable in the index
 * from the ones that needed an answer.
 *
 * What the two surfaces share with the broker's elections page is still
 * `enrollment/electionCore.ts` — tier resolution, dependant pricing, the flex
 * arithmetic, the leave bounds and the PUT payload. That is the part that has
 * to agree, and it is the only part that can. */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import type {
  ElectionIn,
  EnrollmentSubmitInput,
  ProductTierSet,
} from "@/api/enrollment";
import type { PortalEnrollmentData } from "@/api/portal";
import {
  type DependantRef,
  type ProductState,
  baselineElectionState,
  buildElectionsPayload,
  computeFlex,
  flexShort,
  heldElectionState,
  leaveTrade,
  sameElection,
  seedElectionState,
} from "@/components/enrollment/electionCore";
import { Deck, type DeckSlide } from "@/components/portal/leaf/Deck";
import { HeadRail, useHeadRailWidth } from "@/components/portal/leaf/HeadRail";
import { Mount, glassHover, glassSurface } from "@/components/portal/leaf/Mount";
import { Strike } from "@/components/portal/leaf/Strike";
import { currencySymbol } from "@/components/portal/leaf/Figure";
import { formatDay } from "@/components/portal/leaf/date";
import {
  HeadBalance,
  RailHeader,
} from "@/components/portal/enrollment/DeckHeader";
import { LeaveMount } from "@/components/portal/enrollment/LeaveMount";
import { ProductElectionMount } from "@/components/portal/enrollment/ProductElectionMount";
import {
  type LeaveChange,
  ReviewMount,
  buildChanges,
} from "@/components/portal/enrollment/ReviewMount";
import {
  type StandardLine,
  StandardMount,
} from "@/components/portal/enrollment/StandardMount";
import { productShortLabel } from "@/components/portal/leaf/glossary";
import { ConflictDetailError, formatError } from "@/lib/errors";
import { fmtAmount } from "@/lib/format";
import { cn } from "@/lib/cn";

/** Slide keys that are not a product code. Namespaced so they can never collide
 * with one — a deck has one key space and it is what the URL carries.
 *
 * There is deliberately no allowance slide. The running balance is in the
 * heading row, visible from every slide, and its working is on the review step
 * with the total it explains — as a slide of its own it opened the deck on a
 * near-empty pane of context instead of on the first decision. */
const STANDARD_KEY = "standard";
const LEAVE_KEY = "leave";
const REVIEW_KEY = "review";

/** Does this product ask the member anything?
 *
 * Deliberately independent of `disabled`: a confirmed enrollment and the
 * broker's preview show the SAME set of slides an open one does, so the record
 * a member reads back has the shape of the form they filled in.
 *
 * A voluntary dependant list counts even when covering someone is free — ticking
 * a name is still an answer. Compulsory family cover does not: it is a fact
 * about the plan, and it survives the fold as a note on the row. */
function isDecisionful(
  ts: ProductTierSet,
  allowDeps: boolean,
  dependantCount: number,
): boolean {
  const planChoice = ts.allow_plan_change && ts.tiers.length > 1;
  const familyChoice =
    allowDeps &&
    dependantCount > 0 &&
    ts.dependant_participation !== "compulsory";
  return planChoice || ts.can_decline || familyChoice;
}

/** Where the member's enrollment stands, struck onto the page.
 *
 * A state on this surface is STRUCK, never badged (The Ink-Over-Tint Rule) —
 * the same construction the claims list uses, so "Sent" here and "Under review"
 * there read as one vocabulary. Rendered in the PREVIEW too: it is a statement
 * of where the enrollment stands rather than an action, and gated on
 * `!readOnly` it made a submitted enrollment indistinguishable from an
 * untouched one, which is the one thing the preview exists to show.
 *
 * It sits ABOVE the deck rather than on a slide, because it is true of the
 * whole enrollment and a member must not have to find the right slide to learn
 * that their choices are locked. */
function StatusNote({
  mark,
  tone,
  children,
}: {
  mark: string;
  tone: "approved" | "review";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        glassSurface,
        glassHover,
        "flex flex-col gap-2 rounded-control p-3 sm:flex-row sm:items-baseline sm:gap-3",
      )}
    >
      <Strike tone={tone} className="shrink-0">
        {mark}
      </Strike>
      <p className="text-row text-label">{children}</p>
    </div>
  );
}

export function MemberEnrollmentPanel({
  data,
  dependants,
  readOnly = false,
  slideKey,
  onSlideKeyChange,
  onSaveElections,
  onSaveLeave,
  onSubmit,
  saving = false,
  savingLeave = false,
  submitting = false,
}: {
  data: PortalEnrollmentData;
  dependants: DependantRef[];
  readOnly?: boolean;
  /** Controlled deck position, carried in the route's `?p=`. Omit entirely and
   *  the deck holds its own — which is what the broker preview needs. */
  slideKey?: string | null;
  onSlideKeyChange?: (key: string) => void;
  onSaveElections?: (elections: ElectionIn[]) => Promise<unknown>;
  onSaveLeave?: (input: { action: string; days: number }) => Promise<unknown>;
  onSubmit?: (input: EnrollmentSubmitInput) => Promise<unknown>;
  saving?: boolean;
  savingLeave?: boolean;
  submitting?: boolean;
}) {
  const { window: win, enrollment, options } = data;
  // Where the running balance goes. `lg` and up the shell offers the middle of
  // the heading row; below it that row does not exist and the balance rides the
  // deck's sticky rail instead. The broker's preview never uses the heading row
  // — its frame has no rail slot, so `HeadRail` would drop the chip inline
  // above the deck as a stray figure.
  const balanceInHead = useHeadRailWidth() && !readOnly;
  // The ISO code, handed to `Money`, which resolves it to the symbol a member
  // writes ("SGD" → "S$"). Only the two places that compose money into a plain
  // STRING — a toast and a dialog description, neither of which can hold a
  // component — resolve it themselves.
  const currency = options?.flex_currency ?? null;
  const money = currencySymbol(currency);

  const productScopeSet = useMemo(
    () => (win?.product_scope?.length ? new Set(win.product_scope) : null),
    [win],
  );
  const tierSets = useMemo<ProductTierSet[]>(() => {
    const all = options?.products ?? [];
    return productScopeSet
      ? all.filter((p) => productScopeSet.has(p.product_code))
      : all;
  }, [options, productScopeSet]);

  const [state, setState] = useState<Record<string, ProductState>>({});
  const [leaveAction, setLeaveAction] = useState("none");
  const [leaveDays, setLeaveDays] = useState("0");

  // The state the server would produce if the member touched nothing — both the
  // seed and, once saved, the thing local edits are compared against to know
  // whether anything is outstanding.
  const saved = useMemo(
    () =>
      enrollment
        ? seedElectionState(enrollment, tierSets)
        : baselineElectionState(tierSets),
    [enrollment, tierSets],
  );
  // What the member HOLDS — the "before" side of every reported change. Not the
  // same as `saved`: a standing override, or an election saved earlier in this
  // window, is already in `saved` and is still a change to their cover.
  const held = useMemo(
    () => heldElectionState(enrollment, tierSets),
    [enrollment, tierSets],
  );
  // `state` is filled by the effect below, so it is empty on the render that
  // first receives the options — and the deck would build its index from an
  // incomplete set of slides for one frame, then rebuild it. Falling back to
  // the seed per product means the first paint is already the right one.
  const current = useMemo(() => {
    const out: Record<string, ProductState> = {};
    for (const ts of tierSets) {
      out[ts.product_code] = state[ts.product_code] ?? saved[ts.product_code];
    }
    return out;
  }, [state, saved, tierSets]);

  useEffect(() => {
    if (!options) return;
    setState(saved);
    setLeaveAction(enrollment?.leave?.action ?? "none");
    setLeaveDays(String(enrollment?.leave?.days ?? 0));
  }, [enrollment, options, saved]);

  if (!win) {
    return (
      <Mount label="Nothing to choose right now">
        <p className="text-row text-label">
          When your company next opens a benefit selection period, this is where
          you&rsquo;ll change your plan, cover your family or trade leave.
          You&rsquo;ll see a marker on this section when it opens.
        </p>
      </Mount>
    );
  }

  const status = enrollment?.status ?? "not_started";
  const finalized = status === "confirmed" || status === "deemed";
  const submitted = status === "submitted";
  const disabled = readOnly || finalized;
  const allowDeps = win.allow_dependant_changes;

  // ── Leave: ONE normalisation, read by the wallet, the mark, the review and
  //    the save. Computed before the wallet, because the wallet has to price
  //    the trade that would actually be SENT. ─────────────────────────────
  const trade = leaveTrade(leaveAction, leaveDays, options?.leave ?? null, options?.member_leave_rate ?? null);
  // A half-made entry ("Buy", no days yet) is NOT a trade and must not be sent
  // as one; a typed value the rules refuse is a different thing entirely, and
  // silently normalising THAT to "no trade" would send an enrollment without
  // the leave the member believes they asked for. So the first becomes "none"
  // and the second blocks the send.
  const leaveError = trade.trading && (!!trade.daysError || !!trade.blockedReason);
  const chosenLeave: { action: "none" | "buy" | "sell"; days: number } =
    trade.trading && trade.enteredDays > 0 && !leaveError
      ? { action: leaveAction === "buy" ? "buy" : "sell", days: trade.enteredDays }
      : { action: "none", days: 0 };
  const savedLeave = {
    action: enrollment?.leave?.action ?? "none",
    days: enrollment?.leave?.days ?? 0,
  };
  // **An invalid entry is an ERROR, not a pending change to "no trade".**
  // Without this clause a member who already sold 2 days and then typed 99 into
  // the field had `chosenLeave` collapse to none, which differs from the stored
  // trade — so Save (which is not gated on `blocked`, and should not be, since
  // the elections are still saveable) wrote `{none, 0}` and DELETED the trade
  // they were editing, while the field still showed 99.
  const leaveDirty =
    !leaveError &&
    (chosenLeave.action !== savedLeave.action || chosenLeave.days !== savedLeave.days);
  const leaveChange: LeaveChange | null =
    chosenLeave.action === "buy" || chosenLeave.action === "sell"
      ? {
          action: chosenLeave.action,
          days: chosenLeave.days,
          impact: trade.rate > 0 ? (trade.isBuy ? -1 : 1) * chosenLeave.days * trade.rate : 0,
        }
      : null;

  // The NORMALISED trade, not the raw field. `computeFlex` clamps a typed value
  // to the member's cap and prices it regardless of validity, so an entry the
  // save path refuses was still drawn from the wallet: the balance and the
  // ledger reported "Leave you sold back S$500" that nothing would ever save,
  // and on a BUY the phantom debit could push the balance short — reporting
  // "your choices cost more than your allowance" about a leave typo.
  const flex = computeFlex(
    options ?? undefined, tierSets, current, dependants, allowDeps,
    chosenLeave.action, String(chosenLeave.days), options?.leave ?? null,
  );

  const electionsDirty = tierSets.some(
    (ts) => !sameElection(current[ts.product_code], saved[ts.product_code]),
  );
  const dirty = electionsDirty || leaveDirty;

  // Leave first: it names a specific field the member can go and fix, and now
  // that the wallet prices only the sendable trade, a shortfall reported here
  // is a genuine one rather than a consequence of the same typo.
  const blocked = leaveError
    ? "Your leave choice needs fixing before you can send."
    : flexShort(flex) && !win.allow_overdraft
      ? "Your choices cost more than your flex dollars — reduce them before sending."
      : null;

  async function saveAll(): Promise<boolean> {
    try {
      if (electionsDirty && onSaveElections) {
        await onSaveElections(
          buildElectionsPayload(current, tierSets, dependants, allowDeps),
        );
      }
      if (leaveDirty && onSaveLeave) await onSaveLeave(chosenLeave);
      return true;
    } catch (e) {
      toast.error(formatError(e));
      return false;
    }
  }

  async function saveOnly() {
    if (await saveAll()) toast.success("Your choices are saved.");
  }

  async function doSubmit() {
    if (!onSubmit) return;
    // Send carries the exact reviewed choices in one atomic request.
    try {
      await onSubmit({
        acknowledgeUnpriced: false,
        elections: tierSets.length
          ? buildElectionsPayload(current, tierSets, dependants, allowDeps)
          : undefined,
        leave: win?.allow_leave ? chosenLeave : undefined,
      });
      toast.success("Sent — you'll be told once it's confirmed.");
    } catch (e) {
      if (e instanceof ConflictDetailError) {
        if (e.detail.code === "unpriced_elections") {
          const products = Array.isArray(e.detail.products)
            ? (e.detail.products as string[])
            : [];
          toast.error(
            products.length
              ? `Pricing is missing for ${products.join(", ")}. Contact your HR team before sending.`
              : "Pricing is missing for one or more choices. Contact your HR team before sending.",
          );
          return;
        }
        if (e.detail.code === "flex_overdrawn") {
          const balance = e.detail.balance;
          toast.error(
            `Your choices exceed your flex dollars${
              typeof balance === "number"
                ? ` by ${money}${fmtAmount(Math.abs(balance))}`
                : ""
            }. Reduce them before sending.`,
          );
          return;
        }
      }
      toast.error(formatError(e));
    }
  }

  // ── The slides ─────────────────────────────────────────────────────────
  const decisions = tierSets.filter((ts) =>
    isDecisionful(ts, allowDeps, dependants.length),
  );
  const standard: StandardLine[] = tierSets
    .filter((ts) => !isDecisionful(ts, allowDeps, dependants.length))
    .map((ts) => {
      const tier = ts.tiers.find(
        (t) => t.key === current[ts.product_code]?.tierKey,
      );
      return {
        code: ts.product_code,
        name: ts.product_name ?? ts.product_code,
        plan: tier?.label ?? null,
        familyNote:
          ts.dependant_participation === "compulsory" && dependants.length > 0
            ? "Your family is covered on this too."
            : null,
      };
    });

  // Built here rather than inline, because `Deck` branches its whole rail
  // treatment on whether a header EXISTS — an element that happens to render
  // nothing would still turn the phone's chip pill into an empty bordered card.
  const railFlex = balanceInHead ? null : flex;
  const railClosesAt = finalized ? null : win.closes_at;
  const railHeader =
    railClosesAt || railFlex ? (
      <RailHeader closesAt={railClosesAt} flex={railFlex} />
    ) : undefined;

  // ONE computation behind the rail's marks, the rail's count and the review's
  // list. They used to be three, over two different product sets, so the index
  // could read "1 change" above a review that listed two.
  const changes = buildChanges(tierSets, current, held, dependants, allowDeps);
  const changedCodes = new Set(changes.map((c) => c.key));

  const slides: DeckSlide[] = [];
  for (const ts of decisions) {
    const ps = current[ts.product_code];
    if (!ps) continue;
    const changed = changedCodes.has(ts.product_code);
    slides.push({
      key: ts.product_code,
      // The rail's own short form, as on the coverage deck — a chip reading
      // "Group Comprehensive General Practitioner" is a paragraph.
      label: productShortLabel(ts.product_code, ts.product_name),
      mark: !changed ? undefined : ps.declined ? "Declined" : "Changed",
      render: () => (
        <ProductElectionMount
          ts={ts}
          ps={ps}
          rise={false}
          disabled={disabled}
          allowDeps={allowDeps}
          dependants={dependants}
          flexOnChange={!!flex?.onChange}
          currency={currency}
          onChange={(next) =>
            setState((s) => ({ ...s, [ts.product_code]: next }))
          }
        />
      ),
    });
  }
  if (standard.length) {
    slides.push({
      key: STANDARD_KEY,
      label: "Included as standard",
      render: () => <StandardMount lines={standard} rise={false} />,
    });
  }
  if (win.allow_leave) {
    slides.push({
      key: LEAVE_KEY,
      label: "Leave",
      // An entry the rules refuse is marked in the INDEX, not only on the slide
      // — it is the one thing here that stops the send, and a member should not
      // have to hunt the deck for it.
      mark: leaveError
        ? "Needs fixing"
        : leaveChange
          ? leaveChange.action === "buy"
            ? "Buying"
            : "Selling"
          : undefined,
      render: () => (
        <LeaveMount
          rise={false}
          action={leaveAction}
          daysValue={leaveDays}
          leave={options?.leave ?? null}
          ratePerDay={options?.member_leave_rate ?? null}
          currency={currency}
          disabled={disabled}
          onActionChange={setLeaveAction}
          onDaysChange={setLeaveDays}
        />
      ),
    });
  }
  const changeCount = changes.length + (leaveChange ? 1 : 0);
  slides.push({
    key: REVIEW_KEY,
    label: disabled ? "What's on record" : "Review and send",
    mark: changeCount ? `${changeCount} change${changeCount === 1 ? "" : "s"}` : undefined,
    render: () => (
      <ReviewMount
        rise={false}
        tierSets={tierSets}
        state={current}
        changes={changes}
        leave={leaveChange}
        flex={flex}
        allowOverdraft={win.allow_overdraft}
        currency={currency}
        disabled={disabled}
        brokerNote={readOnly && !finalized}
        dirty={dirty}
        saving={saving || savingLeave}
        submitting={submitting}
        blocked={blocked}
        onSave={() => void saveOnly()}
        onSubmit={() => void doSubmit()}
      />
    ),
  });

  return (
    <div className="space-y-4">
      {/* The deadline is furniture on the deck's rail, not a sentence at the
          top of the page: it governs every slide, and as a line in the flow it
          scrolled away with the first product. `win.name` — the broker's
          internal label for the window ("Testing 2026") — is never printed; it
          names a row in their tool, not anything the member has. */}
      {flex && balanceInHead && (
        <HeadRail>
          <HeadBalance flex={flex} />
        </HeadRail>
      )}

      {submitted && (
        <StatusNote mark="Sent" tone="review">
          Your choices were sent
          {enrollment?.submitted_at
            ? ` on ${formatDay(enrollment.submitted_at)}`
            : ""}{" "}
          and are being checked. You can still change them until{" "}
          {formatDay(win.closes_at)}.
        </StatusNote>
      )}
      {finalized && (
        <StatusNote mark="Confirmed" tone="approved">
          These choices are confirmed and your cover is updated. Contact your HR
          team if something needs to change.
        </StatusNote>
      )}

      {decisions.length === 0 && standard.length === 0 && !win.allow_leave ? (
        <Mount label="No plans to change">
          <p className="text-row text-label">
            This period doesn&rsquo;t include any plan you can change. If you
            were expecting a choice here, your HR team can tell you why.
          </p>
        </Mount>
      ) : (
        <Deck
          slides={slides}
          label="Your enrollment"
          itemNoun="step"
          railHeader={railHeader}
          activeKey={slideKey}
          onActiveKeyChange={onSlideKeyChange}
        />
      )}

    </div>
  );
}
