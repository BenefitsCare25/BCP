/** Plan or edit an enrolment period, inline.
 *
 * One form for three moments, because they are the same object at different
 * points in its life:
 * - `create` — a new draft;
 * - `draft`  — every field editable until the period opens;
 * - `open`   — only what the server still accepts once members are inside it
 *   (name, deadline, portal visibility, overdraft). The rest stays visible but
 *   locked, with the reason, rather than disappearing.
 */
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2, Lock } from "lucide-react";
import type { EnrollmentWindow } from "@/api/enrollment";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { SectionLabel } from "@/components/ui/section-label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import {
  type PeriodFormMode,
  type PeriodFormState,
  usePeriodForm,
} from "./usePeriodForm";

type Setter = <K extends keyof PeriodFormState>(key: K, value: PeriodFormState[K]) => void;

interface GroupProps {
  f: PeriodFormState;
  set: Setter;
  locked: boolean;
}

export function PeriodForm({
  mode,
  window,
  policyYearId,
  onDone,
  onCancel,
}: {
  mode: PeriodFormMode;
  window?: EnrollmentWindow;
  policyYearId: string;
  onDone: (w: EnrollmentWindow) => void;
  onCancel?: () => void;
}) {
  const { form: f, set, error, submit, productCodes, locked, pending } = usePeriodForm({
    mode,
    window,
    policyYearId,
    onDone,
  });
  const props = { f, set, locked };
  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <BasicsGroup {...props} />
      <ChooserGroup {...props} />
      <ChangesGroup {...props} productCodes={productCodes} />
      <DefaultGroup {...props} />
      <FlexGroup {...props} />
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button type="submit" disabled={pending || Boolean(error)}>
          {pending && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {mode === "create" ? "Save as draft" : "Save changes"}
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        )}
        {error && <span className="text-xs text-muted-foreground">{error}</span>}
      </div>
    </form>
  );
}

function BasicsGroup({ f, set, locked }: GroupProps) {
  return (
    <Group title="Basics">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-3">
          <Label htmlFor="period-name">Name</Label>
          <Input
            id="period-name"
            value={f.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="2027 annual enrolment"
          />
        </div>
        <div>
          <Label htmlFor="period-opens">Members can start</Label>
          <Input
            id="period-opens"
            type="datetime-local"
            value={f.opensAt}
            disabled={locked}
            onChange={(e) => set("opensAt", e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="period-closes">Deadline</Label>
          <Input
            id="period-closes"
            type="datetime-local"
            value={f.closesAt}
            onChange={(e) => set("closesAt", e.target.value)}
          />
        </div>
        <p className="self-end pb-2 text-xs text-muted-foreground">
          Times are in your local time zone. Nothing closes automatically — the
          deadline locks members out; you close the period.
        </p>
      </div>
    </Group>
  );
}

function ChooserGroup({ f, set }: GroupProps) {
  return (
    <Group title="Who makes the choices">
      <Segmented
        value={f.selfService ? "members" : "brokers"}
        onChange={(v) => set("selfService", v === "members")}
        options={[
          { value: "members", label: "Members, in the portal" },
          { value: "brokers", label: "Brokers only" },
        ]}
      />
      <Note>
        {f.selfService
          ? "Members see this period in their portal and submit their own choices; you confirm them."
          : "The portal shows nothing. You make and confirm every choice on members' behalf."}
      </Note>
    </Group>
  );
}

function ChangesGroup({ f, set, locked, productCodes }: GroupProps & { productCodes: string[] }) {
  return (
    <Group title="What members can change" locked={locked}>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Toggle
          label="Plan (upgrade, downgrade, decline)"
          checked={f.allowPlanChange}
          disabled={locked}
          onChange={(v) => set("allowPlanChange", v)}
        />
        <Toggle
          label="Which dependants are covered"
          checked={f.allowDeps}
          disabled={locked}
          onChange={(v) => set("allowDeps", v)}
        />
        <Toggle
          label="Buy or sell leave"
          checked={f.allowLeave}
          disabled={locked}
          onChange={(v) => set("allowLeave", v)}
        />
      </div>
      {f.allowLeave && (
        <Note>
          Day limits and the per-day price live on{" "}
          <RulesLink section="leave">Pricing &amp; rules → Leave</RulesLink>.
        </Note>
      )}
      <div className="pt-1">
        <SectionLabel as="h4" className="mb-1.5">Products in this period</SectionLabel>
        <div className="flex flex-wrap gap-1.5">
          <Chip selected={f.scope === null} disabled={locked} onClick={() => set("scope", null)}>
            All products
          </Chip>
          {productCodes.map((code) => {
            const on = f.scope?.includes(code) ?? false;
            const cur = f.scope ?? [];
            return (
              <Chip
                key={code}
                selected={on}
                disabled={locked}
                onClick={() => set("scope", on ? cur.filter((c) => c !== code) : [...cur, code])}
              >
                {code}
              </Chip>
            );
          })}
        </div>
      </div>
    </Group>
  );
}

function DefaultGroup({ f, set, locked }: GroupProps) {
  return (
    <Group title="Members who do nothing" locked={locked}>
      <Segmented
        value={f.defaultBehavior}
        disabled={locked}
        onChange={(v) => set("defaultBehavior", v)}
        options={[
          { value: "deemed_keep_current", label: "Keep current plans" },
          { value: "deemed_decline", label: "Decline voluntary cover" },
        ]}
      />
      <Note>
        {f.defaultBehavior === "deemed_keep_current"
          ? "At close, anyone who didn't submit keeps exactly the cover they have today. Saved but unsent changes are discarded unless you submit them when closing."
          : "At close, anyone who didn't submit is removed from voluntary products. Compulsory cover is never removed."}
      </Note>
    </Group>
  );
}

function FlexGroup({ f, set, locked }: GroupProps) {
  return (
    <Group title="Flex wallet">
      <Toggle
        label="Fund choices from members' flex wallets"
        checked={f.usesFlex}
        disabled={locked}
        onChange={(v) => set("usesFlex", v)}
      />
      {!f.usesFlex ? (
        <Note>Members see their plans and premiums but no wallet or price tags.</Note>
      ) : (
        <div className="space-y-3 pt-1">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-foreground">Wallet is charged</span>
            <Segmented
              value={f.drawdown}
              disabled={locked}
              onChange={(v) => set("drawdown", v)}
              options={[
                { value: "full", label: "The full plan price" },
                { value: "on_change", label: "Only the difference" },
              ]}
            />
          </div>
          <Note>
            {f.drawdown === "on_change"
              ? "Only an upgrade's extra cost is charged; a downgrade credits the wallet."
              : "Every plan's full price tag is taken from the wallet."}
          </Note>
          <Toggle
            label="Allow choices that cost more than the wallet holds"
            checked={f.overdraft}
            onChange={(v) => set("overdraft", v)}
          />
          <Note>
            {f.overdraft
              ? "The shortfall is the member's to cover (e.g. through payroll)."
              : "Submitting is refused while choices cost more than the wallet."}{" "}
            Prices are set on <RulesLink>Pricing &amp; rules</RulesLink>.
          </Note>
        </div>
      )}
    </Group>
  );
}

function RulesLink({ section, children }: { section?: string; children: ReactNode }) {
  return (
    <Link
      to="/client-relations/enrollment"
      search={{ tab: "rules", ...(section ? { section } : {}) }}
      className="text-foreground underline underline-offset-2"
    >
      {children}
    </Link>
  );
}

function Group({
  title,
  locked = false,
  children,
}: {
  title: string;
  locked?: boolean;
  children: ReactNode;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
        {title}
        {locked && (
          <span className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground">
            <Lock className="size-3" aria-hidden /> Locked once the period opened
          </span>
        )}
      </legend>
      {children}
    </fieldset>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

function Toggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label
      className={cn(
        "flex min-h-8 items-center gap-2 text-sm text-foreground",
        disabled && "text-muted-foreground",
      )}
    >
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      {label}
    </label>
  );
}

function Chip({
  selected,
  disabled,
  onClick,
  children,
}: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "min-h-8 rounded-md border px-2.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        "disabled:cursor-not-allowed disabled:opacity-60",
        selected
          ? "border-foreground bg-foreground text-background"
          : "border-border bg-card text-foreground hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}
