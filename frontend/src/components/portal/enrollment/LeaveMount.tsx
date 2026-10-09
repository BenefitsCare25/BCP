import { portalText, usePortalTranslation } from "@/i18n/portal";
/** Buying and selling leave, as a mount.
 *
 * Every rule the server enforces (`enrollment_validation.validate_leave`) is
 * evaluated by `electionCore.leaveTrade` and stated here BEFORE a save — the
 * cap, the minimum, the increment and whether this member may trade at all — so
 * the member never discovers a limit as a 422. The card is legibility; the gate
 * is still server-side.
 *
 * Days are the member's unit and money is the consequence, so both are printed:
 * "3 days" alone does not tell anyone what leaving with them costs.
 *
 * **It has no save button of its own.** It used to, which made leave the one
 * choice on this page that was committed separately from every other — a member
 * could set a trade, press Save on the plans and send an enrollment their leave
 * was not part of. Leave is now written by the same Save and Send that write the
 * elections, from the review step. */
import type { MemberLeaveOptions } from "@/api/enrollment";
import { leaveTrade } from "@/components/enrollment/electionCore";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Money } from "@/components/portal/leaf/Figure";
import { Mount, MountRow } from "@/components/portal/leaf/Mount";

/** "up to 5 days" / "up to 1 day" — a day count is printed as words a member
 * reads, not as a bare number with a hardcoded plural. */
function days(n: number): string {
  return portalText("{0} day{1}", [n, n === 1 ? "" : "s"]);
}

type Trade = ReturnType<typeof leaveTrade>;

/** The traded days and what they are worth — the same two facts in both the
 *  read-only and the editable shape, so they can't come to disagree. */
function TradeImpact({
  t,
  currency,
  emphasis,
}: {
  t: Trade;
  currency: string | null;
  emphasis?: boolean;
}) {
  const pt = usePortalTranslation();
  return (
    <MountRow
      term={t.isBuy ? "Taken from your flex dollars" : "Added to your flex dollars"}
      gloss={emphasis ? pt("{0} at your daily rate.", [days(t.enteredDays)]) : undefined}
    >
      <Money
        value={t.impact}
        currency={currency}
        emphasis={emphasis ? "strong" : undefined}
        className={emphasis && t.isBuy ? "text-strike-pending" : undefined}
      />
    </MountRow>
  );
}

/** The two controls: what to do, and how many days. */
function TradeControls({
  action,
  daysValue,
  t,
  onActionChange,
  onDaysChange,
}: {
  action: string;
  daysValue: string;
  t: Trade;
  onActionChange: (action: string) => void;
  onDaysChange: (days: string) => void;
}) {
  const pt = usePortalTranslation();
  return (
    // One column on a phone — a frame is either full width or it is not on this
    // breakpoint (The Whole-Frame Rule).
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <Field label={pt("What would you like to do")}>
        {(p) => (
          <select
            {...p}
            className={leafControl}
            value={action}
            onChange={(e) => onActionChange(e.target.value)}
          >
            <option value="none">{pt("Nothing")}</option>
            <option value="buy" disabled={!!t.buyBlocked}>
              {pt("Buy extra days")} </option>
            <option value="sell" disabled={!!t.sellBlocked}>
              {pt("Sell days back")} </option>
          </select>
        )}
      </Field>

      <Field
        label={pt("How many days")}
        error={t.daysError?.startsWith("More than") ? pt("More than the {0}-day limit", [t.maxDays])
          : t.daysError?.startsWith("At least") ? pt("At least {0} day{1}", [t.minDays, t.minDays === 1 ? "" : "s"])
          : t.daysError?.startsWith("Must be") ? pt("Must be in {0}-day steps", [t.step]) : pt(t.daysError)}
        hint={
          t.trading
            ? `${
                t.minDays > 0
                  ? pt("{0}–{1} days", [t.minDays, t.maxDays])
                  : pt("Up to {0}", [days(t.maxDays)])
              }${t.step !== 1 ? pt(", in {0}-day steps", [t.step]) : ""}.`
            : undefined
        }
      >
        {(p) => (
          <input
            {...p}
            type="number"
            className={leafControl}
            min={t.minDays}
            max={t.trading ? t.maxDays : undefined}
            step={t.step}
            value={daysValue}
            disabled={!t.trading}
            onChange={(e) => onDaysChange(e.target.value)}
          />
        )}
      </Field>
    </div>
  );
}

/** Why an option is unavailable, and what a missing rate means. Both are
 *  silent server-side outcomes otherwise (a 422, or a $0 draw). */
function TradeNotices({ t }: { t: Trade }) {
  const pt = usePortalTranslation();
  return (
    <>
      {t.blockedReason && (
        <p className="text-row text-strike-pending">{pt(t.blockedReason)}</p>
      )}
      {t.trading && !t.blockedReason && t.rate <= 0 && (
        <p className="text-row text-label">
          {pt("There’s no daily rate set for your role yet, so trading leave won’t change your flex dollars. Your HR team can confirm it.")} </p>
      )}
      {!t.trading && t.buyBlocked && t.sellBlocked && (
        <p className="text-row text-label">
          {pt("You can’t buy or sell leave this year.")} </p>
      )}
    </>
  );
}

export function LeaveMount({
  action,
  daysValue,
  leave,
  ratePerDay,
  currency,
  disabled,
  rise = true,
  onActionChange,
  onDaysChange,
}: {
  action: string;
  daysValue: string;
  /** The member's bounds + eligibility (null = no leave policy this year). */
  leave: MemberLeaveOptions | null;
  /** Per-day price of a traded day (null/0 = leave is unpriced). */
  ratePerDay: number | null;
  currency: string | null;
  /** Read-only: the broker preview, or an enrollment already confirmed. */
  disabled: boolean;
  /** Off inside an enrollment-deck slide, whose own transition owns the
   *  arrival — see `Mount`'s `rise`. */
  rise?: boolean;
  onActionChange: (action: string) => void;
  onDaysChange: (days: string) => void;
}) {
  const pt = usePortalTranslation();
  const t = leaveTrade(action, daysValue, leave, ratePerDay);

  // The allowance, stated before anything is picked — the day cap AND what it
  // is worth. Without it a member only learns their limit by exceeding it.
  const allowance = leave
    ? [
        !t.buyBlocked && pt("buy up to {0}", [days(leave.max_buy_days)]),
        !t.sellBlocked && pt("sell up to {0}", [days(leave.max_sell_days)]),
      ]
        .filter(Boolean)
        .join(pt(", or "))
    : "";

  return (
    <Mount
      as="article"
      rise={rise}
      label={pt("Buy or sell leave")}
      gloss="Spend part of your flex dollars on extra days off, or sell days back to add to them."
    >
      {allowance && (
        <p className="text-row text-label">
          {pt("You can")} {allowance}
          {t.rate > 0 && (
            <>
              {pt(" — worth ")}
              <Money value={t.rate} currency={currency} emphasis="strong" />
              {pt(" a day.")}
            </>
          )}
          {t.rate <= 0 && "."}
        </p>
      )}

      {disabled ? (
        <dl>
          <MountRow term="Leave">
            {t.trading && t.enteredDays > 0
              ? pt("{0} {1}", [pt(t.isBuy ? "Bought" : "Sold back"), days(t.enteredDays)])
              : pt("You haven't traded any leave")}
          </MountRow>
          {t.trading && t.rate > 0 && t.enteredDays > 0 && (
            <TradeImpact t={t} currency={currency} />
          )}
        </dl>
      ) : (
        <>
          <TradeControls
            action={action}
            daysValue={daysValue}
            t={t}
            onActionChange={onActionChange}
            onDaysChange={onDaysChange}
          />

          {/* The money view of the elected trade. */}
          {t.trading && t.rate > 0 && t.enteredDays > 0 && !t.daysError && (
            <dl>
              <TradeImpact t={t} currency={currency} emphasis />
            </dl>
          )}

          <TradeNotices t={t} />
        </>
      )}
    </Mount>
  );
}
