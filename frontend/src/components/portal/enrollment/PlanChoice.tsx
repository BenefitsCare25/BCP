/** The plan a member holds, in whichever of three shapes the window allows.
 *
 * **Rows, not a dropdown, and that is the substantive change.** A member comes
 * to this page to answer one question — should I change my plan — and a
 * collapsed `<select>` answers it only for someone who already knows an
 * alternative exists. As rows, the cover and the price of every option are on
 * screen at once and comparable down a column (The Tabular-Figure Rule), the
 * target is 44px by construction, and there is no Radix portal to escape
 * `.leaf` and render the member's own listbox in the broker's tokens.
 *
 * Two shapes are deliberately NOT rows:
 *   - **Nothing to choose** — one option, or a window permitting neither a plan
 *     change nor a decline, or a read-only surface (the broker's employee-view
 *     preview, a confirmed enrollment). The outcome is PRINTED. A radio group
 *     naming its only member is chrome describing nothing, and a column of
 *     greyed radios is both harder to read and, at the opacity that makes
 *     "disabled" legible as a state, under the contrast floor.
 *   - **More than `MAX_CHOICE_ROWS` options** — a GPA product can carry twenty
 *     voluntary levels, and twenty rows is a wall rather than a comparison.
 *     Those fall back to a native select (never Radix, for the reason above)
 *     with the selection's figures printed beneath it. */
import { useId } from "react";
import type { CohortTier, ProductTierSet } from "@/api/enrollment";
import {
  type ProductState,
  directionLabel,
} from "@/components/enrollment/electionCore";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Money } from "@/components/portal/leaf/Figure";
import { MountRow } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "./choiceRow";
import { TierDifferences } from "./TierDifferences";
import { cn } from "@/lib/cn";

/** Above this, the options stop being comparable and become a list to search. */
const MAX_CHOICE_ROWS = 6;

/** The sentinel row value for "I don't want this cover" — a real option in the
 * same group as the tiers rather than a checkbox beside them, because declining
 * and electing a tier are mutually exclusive and a separate control only
 * implied it. */
const DECLINE = "__decline__";

/** A term/figure pair inside a choice row. Not `MountRow` — that one owns the
 * mount's own left and right margins (The One-Left-Edge Rule), and these sit
 * one indent step in, beside their radio. */
function ChoiceFigure({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <dt className="min-w-0 text-row text-label">{term}</dt>
      <dd className="shrink-0 text-row text-record">{children}</dd>
    </div>
  );
}

function ChoiceRow({
  name,
  value,
  checked,
  onSelect,
  title,
  note,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  /** The right-hand marker on the title line: direction, or nothing. */
  note?: string | null;
  /** What this option costs and covers. */
  children?: React.ReactNode;
}) {
  return (
    <label className={choiceRowClass(checked)}>
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={onSelect}
        className={cn(choiceControl, "mt-0.5")}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <span
            className={cn(
              "min-w-0 text-row text-record",
              checked && "font-semibold",
            )}
          >
            {title}
          </span>
          {note && <span className="shrink-0 text-row text-label">{note}</span>}
        </div>
        {children && <div className="mt-1">{children}</div>}
      </div>
    </label>
  );
}

/** What electing this tier does to the allowance, in the member's words.
 *
 * A zero price is a SENTENCE, never a term with a figure beside it. Rendered as
 * a row it printed "No change to your allowance ———" against the right margin,
 * and an em-dash in a figure column is this portal's mark for a value we do not
 * have (`Money` renders one for null) — so the one row stating that a plan is
 * free read as the one row whose price we had failed to load.
 *
 * `null` when the product draws no flex at all: there is nothing to say, and
 * "S$0" would invite the member to look for money that was never in play. */
function priceTerm(
  tier: CohortTier,
  flexOnChange: boolean,
): { term: string; amount: number | null } | null {
  if (tier.price_tag == null) return null;
  if (tier.price_tag === 0)
    return {
      term: flexOnChange
        ? "No change to your allowance."
        : "Nothing comes out of your allowance.",
      amount: null,
    };
  if (tier.price_tag > 0) return { term: "Costs you", amount: tier.price_tag };
  return { term: "Adds back", amount: Math.abs(tier.price_tag) };
}

/** The cover and the price of one tier.
 *
 * `sum_insured` is present on products that pay a STATED AMOUNT (life, critical
 * illness, personal accident) and absent on reimbursement products (GP,
 * specialist, dental, hospital), whose entitlement is a schedule of benefits
 * rather than one figure — so a missing row here is the product's shape, not a
 * gap in what we loaded. The premium fields never arrive on this surface at all
 * (`build_portal_enrollment` scrubs them), so the covered amount plus the price
 * tag is the whole of what a member can act on. */
function TierFigures({
  tier,
  flexOnChange,
  currency,
}: {
  tier: CohortTier;
  flexOnChange: boolean;
  currency: string | null;
}) {
  const si = tier.financials?.sum_insured ?? null;
  const price = priceTerm(tier, flexOnChange);
  if (si == null && !price) return null;
  return (
    <>
      {(si != null || price?.amount != null) && (
        <dl>
          {si != null && (
            <ChoiceFigure term="You'd be covered for">
              <Money value={si} currency={currency} />
            </ChoiceFigure>
          )}
          {price?.amount != null && (
            <ChoiceFigure term={price.term}>
              <Money value={price.amount} currency={currency} />
            </ChoiceFigure>
          )}
        </dl>
      )}
      {price && price.amount == null && (
        <p className="text-row text-label">{price.term}</p>
      )}
    </>
  );
}

export function PlanChoice({
  ts,
  ps,
  disabled,
  flexOnChange,
  currency,
  onChange,
}: {
  ts: ProductTierSet;
  ps: ProductState;
  disabled: boolean;
  flexOnChange: boolean;
  currency: string | null;
  onChange: (next: ProductState) => void;
}) {
  const groupName = useId();

  const selectedTier = ts.tiers.find((t) => t.key === ps.tierKey) ?? null;
  // A window that forbids plan changes still allows a decline, so the member's
  // own tier stays on offer as the alternative to declining — it is just not
  // swappable for a sibling.
  const offered = ts.allow_plan_change
    ? ts.tiers
    : selectedTier
      ? [selectedTier]
      : [];
  const optionCount = offered.length + (ts.can_decline ? 1 : 0);
  const editable = !disabled && optionCount > 1;
  const asRows = editable && optionCount <= MAX_CHOICE_ROWS;

  const selectTier = (key: string) =>
    onChange({ ...ps, tierKey: key, declined: false });
  // Names the "before" side of every difference below, so the member is not
  // left inferring which of two values is the one they hold today.
  const currentLabel = ts.tiers.find((t) => t.is_baseline)?.label ?? null;

  if (asRows) {
    return (
      <div className="flex flex-col gap-1.5">
        <h3 className="leaf-label" id={`${groupName}-label`}>
          Your plan
        </h3>
        <div
          role="radiogroup"
          aria-labelledby={`${groupName}-label`}
          className="flex flex-col"
        >
          {offered.map((t) => (
            <ChoiceRow
              key={t.key}
              name={groupName}
              value={t.key}
              checked={!ps.declined && ps.tierKey === t.key}
              onSelect={() => selectTier(t.key)}
              title={`${t.label}${t.is_baseline ? " — your current plan" : ""}`}
              // Neutral ink, deliberately. The strike ramp belongs to claim
              // verdicts (The Status-Is-Not-Brand Rule), and a green "upgrade"
              // beside an approved claim's green would ask the member to decide
              // which kind of green they are reading.
              note={t.is_baseline ? null : directionLabel(t.direction, true)}
            >
              <TierFigures
                tier={t}
                flexOnChange={flexOnChange}
                currency={currency}
              />
              {/* Only on the alternatives: the baseline IS the reference, so a
                  "what changes" block under it would compare it to itself. */}
              <TierDifferences
                differences={t.differences ?? []}
                total={t.differences_total ?? 0}
                currentLabel={currentLabel}
                electedLabel={t.label}
              />
            </ChoiceRow>
          ))}
          {ts.can_decline && (
            <ChoiceRow
              name={groupName}
              value={DECLINE}
              checked={ps.declined}
              onSelect={() => onChange({ ...ps, declined: true })}
              title="I don't want this cover"
            >
              <p className="text-row text-label">
                You won&rsquo;t be covered under this plan.
              </p>
            </ChoiceRow>
          )}
        </div>
      </div>
    );
  }

  if (editable) {
    // The long-list fallback. A native control, so the options render in the
    // member's own world and the phone gives them its wheel.
    return (
      <>
        <Field label="Your plan">
          {(p) => (
            <select
              {...p}
              className={leafControl}
              value={ps.declined ? DECLINE : ps.tierKey}
              onChange={(e) =>
                e.target.value === DECLINE
                  ? onChange({ ...ps, declined: true })
                  : selectTier(e.target.value)
              }
            >
              {offered.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                  {t.is_baseline ? " — your current plan" : ""}
                </option>
              ))}
              {ts.can_decline && (
                <option value={DECLINE}>I don't want this cover</option>
              )}
            </select>
          )}
        </Field>
        {!ps.declined && selectedTier && (
          <>
            <TierFigures
              tier={selectedTier}
              flexOnChange={flexOnChange}
              currency={currency}
            />
            <TierDifferences
              differences={selectedTier.differences ?? []}
              total={selectedTier.differences_total ?? 0}
              currentLabel={currentLabel}
              electedLabel={selectedTier.label}
            />
          </>
        )}
      </>
    );
  }

  // Nothing to choose, or nothing choosable any more: print the outcome.
  // `MountRow` here rather than `ChoiceFigure` — with no radio to sit beside,
  // these rows belong on the mount's own left and right margins.
  const selectedSi = selectedTier?.financials?.sum_insured ?? null;
  const selectedPrice = selectedTier
    ? priceTerm(selectedTier, flexOnChange)
    : null;
  return (
    <>
      <dl>
        {ps.declined ? (
          <MountRow term="Your plan">You&rsquo;ve declined this cover</MountRow>
        ) : selectedTier ? (
          <>
            <MountRow
              term="Your plan"
              gloss={
                selectedTier.is_baseline
                  ? undefined
                  : (directionLabel(selectedTier.direction, true) ?? undefined)
              }
            >
              {selectedTier.label}
            </MountRow>
            {selectedSi != null && (
              <MountRow term="You're covered for">
                <Money value={selectedSi} currency={currency} />
              </MountRow>
            )}
            {selectedPrice?.amount != null && (
              <MountRow term={selectedPrice.term}>
                <Money value={selectedPrice.amount} currency={currency} />
              </MountRow>
            )}
          </>
        ) : (
          <MountRow term="Your plan">Not set</MountRow>
        )}
      </dl>
      {!ps.declined && selectedPrice && selectedPrice.amount == null && (
        <p className="text-row text-label">{selectedPrice.term}</p>
      )}
      {/* A confirmed or previewed enrollment can sit on a non-baseline
          tier, and "what changed from the plan you had" is still the
          clearest statement of what was elected. Empty on the baseline. */}
      {!ps.declined && selectedTier && (
        <TierDifferences
          differences={selectedTier.differences ?? []}
          total={selectedTier.differences_total ?? 0}
          currentLabel={currentLabel}
          electedLabel={selectedTier.label}
          settled
        />
      )}
    </>
  );
}
