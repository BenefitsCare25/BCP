/** The last slide: everything the member is about to send, and the two buttons
 * that send it.
 *
 * **A deck earns its index by hiding what it is not showing, and that is a debt
 * a form has to repay before it commits.** On the coverage tab an unread slide
 * costs the reader nothing; here it is a decision they may not remember making,
 * on a page whose whole output is a change to their insurance. So the deck ends
 * where a checkout ends: one place that states, in the member's own words, what
 * differs from the cover they hold today — and nothing else, because a summary
 * that reprints the choices is just the long page again.
 *
 * The "before" side is what the member HOLDS (`heldElectionState`), never what
 * was last saved. A member who elected an upgrade yesterday and came back would
 * otherwise be told they are changing nothing, while the enrollment they are
 * about to send changes their cover.
 *
 * ## Send saves first, and that is a fix, not a convenience
 *
 * `POST /portal/enrollment/submit` carries no elections — it submits whatever is
 * STORED. The old page had "Save my choices" and "Send them in" side by side and
 * nothing joining them, so changing a plan and pressing Send submitted the
 * PREVIOUS elections and reported success. Send now writes the elections (and
 * the leave trade) before it submits, and Save is what it says it is: somewhere
 * to stop for now. */
import { Loader2, Send } from "lucide-react";
import type { ProductTierSet } from "@/api/enrollment";
import {
  type DependantRef,
  type FlexSummary,
  type ProductState,
  sameElection,
} from "@/components/enrollment/electionCore";
import { Action } from "@/components/portal/leaf/Action";
import { Money } from "@/components/portal/leaf/Figure";
import { Mount, MountRow, MountRule } from "@/components/portal/leaf/Mount";
import { WalletLedger } from "./WalletLedger";
import { cn } from "@/lib/cn";

/** The two sides of one product's change, already worded. */
export interface ChangeLine {
  key: string;
  product: string;
  from: string;
  to: string;
  /** Who joined or left the plan, when that is what moved. */
  family: string | null;
}

/** "A", "A and B", "A, B and C" — a list a member reads aloud, not a
 *  comma-joined array. */
function list(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** What moved on the family side of one product.
 *
 * A name we cannot resolve says "someone in your family" rather than printing
 * an id: the member is being asked to confirm this, and an id confirms nothing.
 * A dependant removed from the roster between the window opening and now is
 * exactly the case that produces one. */
function familyChange(
  from: ProductState,
  to: ProductState,
  dependants: DependantRef[],
): string | null {
  const nameOf = (id: string) =>
    dependants.find((d) => d.id === id)?.name ?? "someone in your family";
  const added = to.dependantIds
    .filter((id) => !from.dependantIds.includes(id))
    .map(nameOf);
  const removed = from.dependantIds
    .filter((id) => !to.dependantIds.includes(id))
    .map(nameOf);
  const levels = [
    ...new Set([
      ...Object.keys(from.depOptionIds),
      ...Object.keys(to.depOptionIds),
    ]),
  ].filter((role) => (from.depOptionIds[role] ?? "") !== (to.depOptionIds[role] ?? ""));

  const parts: string[] = [];
  if (added.length) parts.push(`Adding ${list(added)}`);
  if (removed.length) parts.push(`Removing ${list(removed)}`);
  if (levels.length) parts.push(`New cover level for your ${list(levels)}`);
  return parts.length ? parts.join(" · ") : null;
}

function tierLabel(ts: ProductTierSet, ps: ProductState): string {
  if (ps.declined) return "Not taking this cover";
  return ts.tiers.find((t) => t.key === ps.tierKey)?.label ?? "Not set";
}

/** Whether the member can elect who this product covers. Compulsory family
 *  cover is a fact about the plan, not an answer — see `sameElection`. */
function familyElectable(ts: ProductTierSet, allowDeps: boolean): boolean {
  return allowDeps && ts.dependant_participation !== "compulsory";
}

/** Everything that differs from the cover the member holds today.
 *
 * **Exported because the rail's marks and its change count are read off this
 * same list.** They were computed separately and over a different set of
 * products (`decisions` rather than all of `tierSets`), so the index could say
 * "1 change" above a review listing two. One function, one answer. */
export function buildChanges(
  tierSets: ProductTierSet[],
  state: Record<string, ProductState>,
  held: Record<string, ProductState>,
  dependants: DependantRef[],
  allowDeps: boolean,
): ChangeLine[] {
  const out: ChangeLine[] = [];
  for (const ts of tierSets) {
    const now = state[ts.product_code];
    const was = held[ts.product_code];
    const ignoreDependants = !familyElectable(ts, allowDeps);
    if (!now || !was || sameElection(now, was, { ignoreDependants })) continue;
    out.push({
      key: ts.product_code,
      product: ts.product_name ?? ts.product_code,
      from: tierLabel(ts, was),
      to: tierLabel(ts, now),
      // A declined product covers nobody, so who used to be on it is not a
      // change worth reporting beside "Not taking this cover".
      family:
        now.declined || ignoreDependants
          ? null
          : familyChange(was, now, dependants),
    });
  }
  return out;
}

/** The before/after pair, in the shape `TierDifferences` already uses on the
 * product slides — the LABEL is rigid and the VALUE flexes, because a plan name
 * can be a long insurer string while "Now" never is. */
const pairRow =
  "flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-0.5";

function Pair({
  term,
  children,
  strong = false,
}: {
  term: string;
  children: React.ReactNode;
  strong?: boolean;
}) {
  return (
    <div className={pairRow}>
      <span className="shrink-0 text-row text-label">{term}</span>
      <span
        className={cn(
          "min-w-0 flex-1 text-right text-row",
          strong ? "font-semibold text-record" : "text-label",
        )}
      >
        {children}
      </span>
    </div>
  );
}

export interface LeaveChange {
  /** "buy" | "sell" — only ever set for a trade that would actually be sent. */
  action: "buy" | "sell";
  days: number;
  /** Signed flex movement (buy spends, sell credits); 0 when unpriced. */
  impact: number;
}

export function ReviewMount({
  tierSets,
  state,
  changes,
  leave,
  flex,
  allowOverdraft,
  currency,
  disabled,
  brokerNote,
  dirty,
  saving,
  submitting,
  blocked,
  onSave,
  onSubmit,
}: {
  tierSets: ProductTierSet[];
  state: Record<string, ProductState>;
  /** Built by `buildChanges` in the panel, so the rail's marks, its change
   *  count and this list are three readings of ONE computation. */
  changes: ChangeLine[];
  /** The trade that would be sent, or null for none. */
  leave: LeaveChange | null;
  /** The live wallet, or null when the member has no flexible-benefits
   *  allowance. Its running total is in the page's heading row; the WORKING
   *  belongs here, with the total it explains. */
  flex: FlexSummary | null;
  allowOverdraft: boolean;
  currency: string | null;
  /** Read-only: the broker preview, or an enrollment already confirmed. */
  disabled: boolean;
  /** Explain the member→broker hand-off. Set on the broker's employee-view
   *  preview and only while the enrollment is still open — on a confirmed one
   *  it would describe a step that has already happened. */
  brokerNote: boolean;
  /** Local choices differ from what the server has stored. */
  dirty: boolean;
  saving: boolean;
  submitting: boolean;
  /** Why sending is refused, in the member's words — null when it isn't. */
  blocked: string | null;
  onSave: () => void;
  onSubmit: () => void;
}) {
  const nothing = changes.length === 0 && !leave;

  return (
    <Mount
      as="article"
      label={disabled ? "What's on record" : "Review and send"}
      gloss={
        disabled
          ? "The choices recorded against your name for this period."
          : "Everything you've changed from the cover you hold today."
      }
    >
      {/* **A settled enrollment states the RECORD, not a diff**, and that is
          not a copy choice. Confirming projects the elections into
          `EmployeePlanOverride`, so `CohortTier.is_current` — which is what
          `heldElectionState` reads — then points at the tier that was ELECTED:
          held equals current and `changes` is empty for everyone. A member who
          upgraded and had it confirmed was therefore told "You haven't changed
          anything… you can still send that in", under a heading reading
          "What's on record", with no button to press. */}
      {disabled ? (
        <dl>
          {tierSets.map((ts) => {
            const ps = state[ts.product_code];
            if (!ps) return null;
            return (
              <MountRow
                key={ts.product_code}
                term={ts.product_name ?? ts.product_code}
              >
                {tierLabel(ts, ps)}
              </MountRow>
            );
          })}
          {leave && (
            <MountRow term="Leave">
              {leave.action === "buy" ? "Bought" : "Sold back"} {leave.days}{" "}
              {leave.days === 1 ? "day" : "days"}
            </MountRow>
          )}
        </dl>
      ) : nothing ? (
        <p className="text-row text-label">
          You haven&rsquo;t changed anything. Your cover carries on exactly as it
          is — you can still send that in to confirm it&rsquo;s what you want.
        </p>
      ) : (
        <dl className="divide-y divide-hairline/75 border-t border-hairline/75">
          {changes.map((c) => (
            <div key={c.key} className="py-2.5">
              <dt className="text-row font-medium text-record">{c.product}</dt>
              <dd className="mt-1.5">
                <Pair term="Now">{c.from}</Pair>
                <Pair term="You've chosen" strong>
                  {c.to}
                </Pair>
                {c.family && (
                  <p className="mt-1 text-row text-label">{c.family}</p>
                )}
              </dd>
            </div>
          ))}
          {leave && (
            <div className="py-2.5">
              <dt className="text-row font-medium text-record">Leave</dt>
              <dd className="mt-1.5">
                <Pair term="You've chosen" strong>
                  {leave.action === "buy" ? "Buying" : "Selling back"}{" "}
                  {leave.days} {leave.days === 1 ? "day" : "days"}
                </Pair>
                {/* Only when it is priced. A zero here would read as free
                    leave rather than as leave whose rate isn't set — the same
                    distinction the plan rows draw with their zero price. */}
                {leave.impact !== 0 && (
                  <Pair
                    term={
                      leave.impact < 0
                        ? "Taken from your allowance"
                        : "Added to your allowance"
                    }
                  >
                    <Money
                      value={Math.abs(leave.impact)}
                      currency={currency}
                    />
                  </Pair>
                )}
              </dd>
            </div>
          )}
        </dl>
      )}

      {flex && (
        <>
          {/* A rule, not a nested card — a mount inside a mount is banned in
              this world — and no heading above it either: the ledger's first
              row is already "Your allowance", and a section label repeating it
              verbatim reads as a rendering fault. */}
          <MountRule />
          <WalletLedger flex={flex} allowOverdraft={allowOverdraft} />
        </>
      )}

      {!disabled && (
        <>
          {/* Stated before the buttons, because it changes what pressing one
              means. A member who has typed changes and walks away loses them,
              and nothing on a deck says so — the old page at least kept its
              save button in view at the bottom of the one column. */}
          <p className="text-row text-label">
            {dirty
              ? "These choices aren't saved yet. Sending saves them too."
              : "Your choices are saved. Sending them starts the check."}
          </p>

          <div className="flex flex-col gap-2 sm:flex-row">
            {/* Only while there is something to save: a permanently dead
                control is furniture, and the line above already says which of
                the two states the member is in. */}
            {dirty && (
              <Action
                block="phone"
                onClick={onSave}
                disabled={saving || submitting}
              >
                {saving && (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                )}
                Save and finish later
              </Action>
            )}
            {/* The page's one brand-coloured fill. */}
            <Action
              tone="primary"
              block="phone"
              disabled={submitting || saving || !!blocked}
              onClick={onSubmit}
            >
              {submitting ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Send className="size-4" aria-hidden />
              )}
              Send them in
            </Action>
          </div>

          {blocked && (
            <p className="text-row text-strike-pending">{blocked}</p>
          )}
        </>
      )}

      {brokerNote && (
        <p className="text-row text-label">
          Members save and send their choices here; a broker then confirms them
          to apply the changes.
        </p>
      )}
    </Mount>
  );
}
