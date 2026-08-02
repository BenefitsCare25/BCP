/** Who else this plan covers, and what covering them costs.
 *
 * Three states, and they are not variations of one control:
 *   - **Compulsory cover** — the family is on the plan by the slip's rule.
 *     There is no choice, so the names are PRINTED. Ticked-and-disabled boxes
 *     drew a control that could not be operated and read as a bug.
 *   - **Read-only** (broker preview, confirmed enrollment) — the covered names
 *     only, for the same reason.
 *   - **Voluntary and open** — the tick list that spends the allowance. */
import type { ProductTierSet } from "@/api/enrollment";
import {
  type DependantRef,
  type ProductState,
  classifyRel,
  dependantPricing,
} from "@/components/enrollment/electionCore";
import { Field, leafControl } from "@/components/portal/leaf/Field";
import { Money, currencySymbol, moneyText } from "@/components/portal/leaf/Figure";
import { MountRow } from "@/components/portal/leaf/Mount";
import { choiceControl, choiceRowClass } from "./choiceRow";
import { cn } from "@/lib/cn";

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
  const depCompulsory = ts.dependant_participation === "compulsory";
  const covered = depCompulsory
    ? dependants
    : dependants.filter((d) => ps.dependantIds.includes(d.id));
  const pricing = dependantPricing(
    ts.dependant, ps.tierKey, ps.dependantIds, dependants, ps.depOptionIds,
  );

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
            ? "Everyone here is covered on this plan automatically — it costs you nothing extra."
            : disabled
              ? "The people covered on this plan alongside you."
              : "Tick anyone you'd like covered on this plan. Doing so spends part of your allowance."}
        </p>

        {depCompulsory || disabled ? (
          covered.length ? (
            <dl>
              {covered.map((d) => (
                <MountRow key={d.id} term={d.name ?? d.id}>
                  <span className="text-label">
                    {d.relationship ?? "Covered"}
                  </span>
                </MountRow>
              ))}
            </dl>
          ) : (
            <p className="text-row text-label">
              Nobody in your family is on this plan.
            </p>
          )
        ) : (
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
                    <span className="shrink-0 text-row text-label">
                      {d.relationship}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        )}
      </div>

      {/* Freestanding dependant cover LEVELS (the slip lists several, unlinked
          to employee plans) — one elected per role; each covered dependant then
          draws that level's rate on their own age band. */}
      {!depCompulsory &&
        (ts.dependant?.option_choices ?? [])
          .filter((r) =>
            ps.dependantIds.some(
              (id) =>
                classifyRel(dependants.find((d) => d.id === id)?.relationship) ===
                r.role,
            ),
          )
          .map((r) => {
            const chosen = ps.depOptionIds[r.role] ?? "";
            const choice = r.choices.find((c) => c.category_id === chosen);
            if (disabled) {
              return (
                <dl key={r.role}>
                  <MountRow term={`Cover for your ${r.role}`}>
                    {choice ? choice.label : "Not chosen"}
                  </MountRow>
                </dl>
              );
            }
            return (
              <Field
                key={r.role}
                label={`How much cover for your ${r.role}`}
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
                        depOptionIds: {
                          ...ps.depOptionIds,
                          [r.role]: e.target.value,
                        },
                      })
                    }
                  >
                    <option value="">Choose one…</option>
                    {r.choices.map((c) => (
                      <option key={c.category_id} value={c.category_id}>
                        {c.label}
                        {/* An `<option>` cannot carry a component, so the money
                            is composed here — with the symbol, and
                            unabbreviated, exactly as `Money` would set it (The
                            Tabular-Figure Rule applies to a listbox too). */}
                        {c.sum_insured != null
                          ? ` — covered for ${currencySymbol(currency)}${moneyText(c.sum_insured)}`
                          : ""}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            );
          })}

      {/* What the family costs. "Draws nothing" is only said when the price is
          KNOWN to be zero — an unresolved price is $0 in the arithmetic but not
          an answer, and asserting free cover beside this mount's own "pick a
          level" hint is how a member reaches a submit that 409s
          `unpriced_elections` at them. */}
      {!depCompulsory &&
        ts.dependant &&
        ts.dependant.mode !== "none" &&
        ps.dependantIds.length > 0 && (
          <p className="text-row text-label">
            {pricing.unresolved ? (
              "We'll show what covering them costs once the level above is chosen."
            ) : pricing.total > 0 ? (
              <>
                Covering them costs you{" "}
                <Money
                  value={pricing.total}
                  currency={currency}
                  emphasis="strong"
                />{" "}
                from your allowance.
              </>
            ) : (
              "Covering them draws nothing from your allowance."
            )}
          </p>
        )}
    </>
  );
}
