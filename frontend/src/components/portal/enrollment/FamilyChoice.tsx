/** Who else this plan covers, and what covering them costs.
 *
 * Three states, and they are not variations of one control:
 *   - **Compulsory cover** — the family is on the plan by the slip's rule.
 *     There is no choice, so the names are PRINTED. Ticked-and-disabled boxes
 *     drew a control that could not be operated and read as a bug.
 *   - **Read-only** (broker preview, confirmed enrollment) — the covered names
 *     only, for the same reason.
 *   - **Voluntary and open** — the tick list that spends the allowance. */
import type { DependantOptionRole, ProductTierSet } from "@/api/enrollment";
import {
  type DependantRef,
  type ProductState,
  classifyRel,
  dependantParticipationFor,
  dependantPricing,
} from "@/components/enrollment/electionCore";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Money, currencySymbol, moneyText } from "@/components/portal/leaf/Figure";
import { MountRow } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "./choiceRow";
import { cn } from "@/lib/cn";

/** The names on the plan, printed — for compulsory cover and read-only views. */
function CoveredNames({ covered }: { covered: DependantRef[] }) {
  if (!covered.length) {
    return (
      <p className="text-row text-label">Nobody in your family is on this plan.</p>
    );
  }
  return (
    <dl>
      {covered.map((d) => (
        <MountRow key={d.id} term={d.name ?? d.id}>
          <span className="text-label">{d.relationship ?? "Covered"}</span>
        </MountRow>
      ))}
    </dl>
  );
}

/** The tick list that puts a dependant on the plan (and spends the allowance). */
function DependantTicks({
  dependants,
  ps,
  onChange,
}: {
  dependants: DependantRef[];
  ps: ProductState;
  onChange: (next: ProductState) => void;
}) {
  return (
    <div className="flex flex-col">
      {dependants.map((d) => {
        const on = ps.dependantIds.includes(d.id);
        return (
          <label key={d.id} className={choiceRowClass(on, "items-center")}>
            <input
              type="checkbox"
              checked={on}
              onChange={(e) =>
                onChange({
                  ...ps,
                  dependantIds: e.target.checked
                    ? [...ps.dependantIds, d.id]
                    : ps.dependantIds.filter((x) => x !== d.id),
                })
              }
              className={choiceControl}
            />
            <span
              className={cn(
                "min-w-0 flex-1 text-row text-record",
                on && "font-semibold",
              )}
            >
              {d.name ?? d.id}
            </span>
            {d.relationship && (
              <span className="shrink-0 text-row text-label">{d.relationship}</span>
            )}
          </label>
        );
      })}
    </div>
  );
}

/** One option level as a single `<option>` line: cover AND price.
 *
 * An `<option>` cannot carry a component, so the money is composed here — with
 * the symbol and unabbreviated, exactly as `Money` would set it (The
 * Tabular-Figure Rule applies to a listbox too).
 *
 * **The price belongs on every line, not only under the chosen one.** Choosing
 * a level IS a price comparison, and with the cost shown only after selection a
 * member had to select each level in turn to find out what it cost. An
 * age-banded level has no single price to print, so it says so rather than
 * printing one dependant's band as if it were everyone's. */
function optionLabel(
  choice: { label: string; sum_insured: number | null; amount: number | null;
    amounts_by_dependant: Record<string, number | null> },
  currency: string | null,
): string {
  const parts = [choice.label];
  if (choice.sum_insured != null) {
    parts.push(`covered for ${currencySymbol(currency)}${moneyText(choice.sum_insured)}`);
  }
  if (choice.amount != null) {
    parts.push(
      choice.amount > 0
        ? `deducts ${currencySymbol(currency)}${moneyText(choice.amount)} from your flex wallet`
        : "no flex deducted",
    );
  } else if (Object.keys(choice.amounts_by_dependant).length) {
    parts.push("price depends on their age");
  }
  return parts.join(" — ");
}

/** The freestanding cover LEVEL elected per role (the slip lists several,
 *  unlinked to employee plans); each covered dependant then draws that level's
 *  rate on their own age band. */
function OptionLevel({
  role,
  ps,
  disabled,
  currency,
  onChange,
}: {
  role: DependantOptionRole;
  ps: ProductState;
  disabled: boolean;
  currency: string | null;
  onChange: (next: ProductState) => void;
}) {
  const chosen = ps.depOptionIds[role.role] ?? "";
  const choice = role.choices.find((c) => c.category_id === chosen);
  if (disabled) {
    return (
      <dl>
        <MountRow term={`Cover for your ${role.role}`}>
          {choice ? choice.label : "Not chosen"}
        </MountRow>
      </dl>
    );
  }
  return (
    <Field
      label={`How much cover for your ${role.role}`}
      hint={chosen ? undefined : "Pick a level to see what it costs you."}
    >
      {(p) => (
        <select
          {...p}
          className={leafControl}
          value={chosen}
          onChange={(e) =>
            onChange({
              ...ps,
              depOptionIds: { ...ps.depOptionIds, [role.role]: e.target.value },
            })
          }
        >
          <option value="">Choose one…</option>
          {role.choices.map((c) => (
            <option key={c.category_id} value={c.category_id}>
              {optionLabel(c, currency)}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

export function FamilyChoice({
  ts,
  ps,
  disabled,
  dependants,
  currency,
  onChange,
}: {
  ts: ProductTierSet;
  ps: ProductState;
  disabled: boolean;
  dependants: DependantRef[];
  currency: string | null;
  onChange: (next: ProductState) => void;
}) {
  const participation = dependantParticipationFor(ts, ps.tierKey);
  if (participation === null) return null;
  const depCompulsory = participation === "compulsory";
  const covered = depCompulsory
    ? dependants
    : dependants.filter((d) => ps.dependantIds.includes(d.id));
  const coveredIds = covered.map((dependant) => dependant.id);
  const pricing = dependantPricing(
    ts.dependant, ps.tierKey, coveredIds, dependants, ps.depOptionIds,
  );
  const tierMode =
    ts.dependant?.by_tier[ps.tierKey]?.mode ?? ts.dependant?.mode ?? "none";
  // Only covered roles need a level. Compulsory cover includes every eligible
  // dependant automatically, but a linked option level can still require an
  // employee choice before its wallet charge is known.
  const roles = (ts.dependant?.option_choices ?? []).filter((r) =>
        coveredIds.some(
          (id) => classifyRel(dependants.find((d) => d.id === id)) === r.role,
        ),
      );
  const showCost =
    !!ts.dependant &&
    tierMode !== "none" &&
    coveredIds.length > 0;

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <h3 className="leaf-label">
          {depCompulsory ? "Your family, already covered" : "Your family"}
        </h3>
        {/* Printed, not behind a hint: a phone has no hover, and this is the
            sentence that says whether ticking is what puts them on the plan. */}
        <p className="text-row text-label">
          {depCompulsory
            ? "Everyone here is covered automatically. Any dependant charge is deducted from your flex dollars."
            : disabled
              ? "The people covered on this plan alongside you."
              : "Tick anyone you'd like covered on this plan. Doing so spends part of your flex dollars."}
        </p>

        {depCompulsory || disabled ? (
          <CoveredNames covered={covered} />
        ) : (
          <DependantTicks dependants={dependants} ps={ps} onChange={onChange} />
        )}
      </div>

      {roles.map((r) => (
        <OptionLevel
          key={r.role}
          role={r}
          ps={ps}
          disabled={disabled}
          currency={currency}
          onChange={onChange}
        />
      ))}

      {/* What the family costs. "Draws nothing" is only said when the price is
          KNOWN to be zero — an unresolved price is $0 in the arithmetic but not
          an answer, and asserting free cover beside this mount's own "pick a
          level" hint is how a member reaches a submit that 409s
          `unpriced_elections` at them. */}
      {showCost && (
        <p className="text-row text-label">
          {pricing.unresolved ? (
            "We'll show what covering them costs once the level above is chosen."
          ) : pricing.total > 0 ? (
            <>
              Covering them costs you{" "}
              <Money value={pricing.total} currency={currency} emphasis="strong" />{" "}
              from your flex dollars.
            </>
          ) : (
            "Covering them draws nothing from your flex dollars."
          )}
        </p>
      )}
    </>
  );
}
