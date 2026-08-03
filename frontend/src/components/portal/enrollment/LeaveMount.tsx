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
  return `${n} day${n === 1 ? "" : "s"}`;
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
  return (
    <MountRow
      term={t.isBuy ? "Taken from your allowance" : "Added to your allowance"}
      gloss={emphasis ? `${days(t.enteredDays)} at your daily rate.` : undefined}
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
  return (
    // One column on a phone — a frame is either full width or it is not on this
    // breakpoint (The Whole-Frame Rule).
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <Field label="What would you like to do">
        {(p) => (
          <select
            {...p}
            className={leafControl}
            value={action}
            onChange={(e) => onActionChange(e.target.value)}
          >
            <option value="none">Nothing</option>
            <option value="buy" disabled={!!t.buyBlocked}>
              Buy extra days
            </option>
            <option value="sell" disabled={!!t.sellBlocked}>
              Sell days back
            </option>
          </select>
        )}
      </Field>

      <Field
        label="How many days"
        error={t.daysError}
        hint={
          t.trading
            ? `${
                t.minDays > 0
                  ? `${t.minDays}–${t.maxDays} days`
                  : `Up to ${days(t.maxDays)}`
              }${t.step !== 1 ? `, in ${t.step}-day steps` : ""}.`
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
  return (
    <>
      {t.blockedReason && (
        <p className="text-row text-strike-pending">{t.blockedReason}</p>
      )}
      {t.trading && !t.blockedReason && t.rate <= 0 && (
        <p className="text-row text-label">
          There&rsquo;s no daily rate set for your role yet, so trading leave
          won&rsquo;t change your allowance. Your HR team can confirm it.
        </p>
      )}
      {!t.trading && t.buyBlocked && t.sellBlocked && (
        <p className="text-row text-label">
          You can&rsquo;t buy or sell leave this year.
        </p>
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
  onActionChange: (action: string) => void;
  onDaysChange: (days: string) => void;
}) {
  const t = leaveTrade(action, daysValue, leave, ratePerDay);

  // The allowance, stated before anything is picked — the day cap AND what it
  // is worth. Without it a member only learns their limit by exceeding it.
  const allowance = leave
    ? [
        !t.buyBlocked && `buy up to ${days(leave.max_buy_days)}`,
        !t.sellBlocked && `sell up to ${days(leave.max_sell_days)}`,
      ]
        .filter(Boolean)
        .join(", or ")
    : "";

  return (
    <Mount
      as="article"
      label="Buy or sell leave"
      gloss="Spend part of your allowance on extra days off, or sell days back to add to it."
    >
      {allowance && (
        <p className="text-row text-label">
          You can {allowance}
          {t.rate > 0 && (
            <>
              {" — worth "}
              <Money value={t.rate} currency={currency} emphasis="strong" />
              {" a day."}
            </>
          )}
          {t.rate <= 0 && "."}
        </p>
      )}

      {disabled ? (
        <dl>
          <MountRow term="Leave">
            {t.trading && t.enteredDays > 0
              ? `${t.isBuy ? "Bought" : "Sold back"} ${days(t.enteredDays)}`
              : "You haven't traded any leave"}
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
