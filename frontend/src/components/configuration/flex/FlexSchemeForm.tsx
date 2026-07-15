import { useEffect, useMemo, useRef, useState } from "react";
import {
  Plus,
  Save,
  CheckCircle2,
  Trash2,
  AlertTriangle,
  Sparkles,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { FieldLabel } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useAssignFlex,
  useConfirmFlexScheme,
  useDiscardFlexScheme,
  useFlexMembership,
  useFlexRosterVocab,
  useSaveFlexScheme,
  useSuggestFlexMatches,
} from "@/api/hooks";
import {
  CURRENCY_OPTIONS,
  DEFAULT_CURRENCY,
  flexTierReview,
  normalizeFlexBody,
  numOrNull,
  validateFlexScheme,
} from "@/lib/flex";
import { ConflictDetailError, formatError } from "@/lib/errors";
import type {
  FlexScheme,
  FlexSchemeBody,
  FlexTier,
  FlexTierHeadcount,
} from "@/types";
import { FlexTierEditor } from "./FlexTierEditor";
import { toast } from "sonner";

interface Props {
  policyYearId: string;
  scheme: FlexScheme;
}

// Match an editor tier to its reconciled headcount by name (case/space-insensitive).
const tierKey = (name: string | null | undefined) =>
  (name ?? "").trim().toLowerCase();

function emptyTier(): FlexTier {
  return {
    name: "",
    employee_type: { raw: "" },
    limits: [],
    cost_sharing: { employer_pct: 80, employee_pct: 20, exceptions: [] },
    benefit_categories: [],
  };
}

export function FlexSchemeForm({ policyYearId, scheme }: Props) {
  const [body, setBody] = useState<FlexSchemeBody>(() => normalizeFlexBody(scheme.scheme));
  const [dirty, setDirty] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  // Set when confirm is blocked by the "employees with no wallet" coverage guard;
  // holds the count so the acknowledge dialog can show it.
  const [unmatchedWarn, setUnmatchedWarn] = useState<number | null>(null);

  const save = useSaveFlexScheme(policyYearId);
  const confirm = useConfirmFlexScheme(policyYearId);
  const discard = useDiscardFlexScheme(policyYearId);
  const assign = useAssignFlex(policyYearId);
  const suggest = useSuggestFlexMatches(policyYearId);
  const { data: vocab } = useFlexRosterVocab(policyYearId);

  // Re-seed local edit state only when the SERVER scheme genuinely changes
  // (initial load, a re-extract, or a saved round-trip) — not on every refetch.
  // A background refetch (window focus, unrelated invalidation) returns a new
  // object reference with identical content; reseeding on that would silently
  // wipe the broker's unsaved edits.
  const lastServerSig = useRef<string | null>(null);
  useEffect(() => {
    const sig = `${scheme.id}:${JSON.stringify(scheme.scheme)}`;
    if (sig === lastServerSig.current) return;
    lastServerSig.current = sig;
    setBody(normalizeFlexBody(scheme.scheme));
    setDirty(false);
  }, [scheme.id, scheme.scheme]);

  const errors = useMemo(() => validateFlexScheme(body), [body]);
  const meta = body.meta ?? {};

  // Tiers are edited one at a time via tabs. `activeTier` only holds the user's
  // last pick; the render-time clamp below keeps it valid as the list changes.
  const [activeTier, setActiveTier] = useState("0");
  const tiersRef = useRef<HTMLDivElement>(null);

  // Clamp the active tab at render so a shrinking/re-seeded tier list can never
  // select a missing tab (which would show a blank panel for a frame).
  const activeValue = String(
    Math.min(Math.max(Number(activeTier) || 0, 0), Math.max(body.tiers.length - 1, 0)),
  );

  const update = (next: FlexSchemeBody) => {
    setBody(next);
    setDirty(true);
  };
  const setMeta = (partial: Partial<FlexSchemeBody["meta"]>) =>
    update({ ...body, meta: { ...meta, ...partial } });
  const setTier = (i: number, tier: FlexTier) =>
    update({ ...body, tiers: body.tiers.map((t, j) => (j === i ? tier : t)) });

  // Reconciled headcount per tier (server-derived), keyed by tier name so a tab
  // can show its eligible count + family-status breakdown alongside the editor.
  const { data: membership } = useFlexMembership(policyYearId);
  const headcountByName = useMemo(() => {
    const m = new Map<string, FlexTierHeadcount>();
    for (const t of membership?.tiers ?? []) m.set(tierKey(t.name), t);
    return m;
  }, [membership]);

  const addTier = () => {
    const newIndex = body.tiers.length;
    update({ ...body, tiers: [...body.tiers, emptyTier()] });
    setActiveTier(String(newIndex));
    requestAnimationFrame(() =>
      tiersRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  };
  const removeTier = (i: number) => {
    update({ ...body, tiers: body.tiers.filter((_, j) => j !== i) });
    // Keep the same tier selected when an earlier one is removed; the render-time
    // clamp handles removing the active/last tab.
    const cur = Number(activeTier) || 0;
    if (i < cur) setActiveTier(String(cur - 1));
  };

  // Scheme-level dependant age caps (age next-birthday). The UI edits the max
  // per role; min inherits the platform default. This is the scheme-wide default
  // fed to the eligibility engine — a product's Flex-pricing entry overrides it.
  const depLimits = meta.dependant_age_limits ?? {};
  const depMax = (role: "spouse" | "child"): number | "" =>
    depLimits[role]?.max ?? "";
  const setDepMax = (role: "spouse" | "child", value: string) =>
    setMeta({
      dependant_age_limits: {
        ...depLimits,
        [role]: { ...(depLimits[role] ?? {}), max: numOrNull(value) },
      },
    });

  const onSave = () =>
    save.mutate(body, {
      onSuccess: () => {
        setDirty(false);
        toast.success("Flex scheme saved");
      },
      onError: (e) => toast.error(formatError(e)),
    });

  const runConfirm = (acknowledge: boolean) =>
    confirm.mutate(acknowledge, {
      onSuccess: () => {
        setDirty(false);
        setUnmatchedWarn(null);
        toast.success("Flex scheme confirmed — wallets assigned to staff");
      },
      onError: (e) => {
        // The coverage guard returns a coded 409 — offer to confirm anyway
        // instead of a flat error toast.
        if (
          e instanceof ConflictDetailError &&
          e.detail.code === "unmatched_employees"
        ) {
          const n = e.detail.ineligible_count;
          setUnmatchedWarn(typeof n === "number" ? n : 0);
          return;
        }
        toast.error(formatError(e));
      },
    });

  const onConfirm = () => {
    if (errors.length > 0) {
      toast.error("Resolve the validation issues before confirming.");
      return;
    }
    // Persist any pending edits first, then confirm.
    save.mutate(body, {
      onSuccess: () => runConfirm(false),
      onError: (e) => toast.error(formatError(e)),
    });
  };

  const onAssign = () =>
    assign.mutate(undefined, {
      onSuccess: (r) =>
        toast.success(
          `Assigned wallets to ${r.employees_assigned.toLocaleString()} of ` +
            `${r.employees_total.toLocaleString()} employees`,
        ),
      onError: (e) => toast.error(formatError(e)),
    });

  const onDiscard = () =>
    discard.mutate(undefined, {
      onSuccess: () => toast.success("Flex scheme discarded"),
      onError: (e) => toast.error(formatError(e)),
    });

  const onSuggest = () =>
    // Persist pending edits first so they aren't lost when the server scheme
    // re-seeds; seeding preserves already-reconciled tiers and only fills empties.
    save.mutate(body, {
      onSuccess: () =>
        suggest.mutate(undefined, {
          onSuccess: () => {
            setDirty(false);
            toast.success("Match suggestions pulled from the roster");
          },
          onError: (e) => toast.error(formatError(e)),
        }),
      onError: (e) => toast.error(formatError(e)),
    });

  const busy =
    save.isPending ||
    confirm.isPending ||
    discard.isPending ||
    assign.isPending ||
    suggest.isPending;

  return (
    <div className="space-y-4">
      {/* Scheme details (meta) */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="font-medium text-foreground">Scheme details</div>
          <div className="flex flex-wrap items-end gap-x-3 gap-y-4">
            <div className="flex-1 min-w-[12rem] space-y-1">
              <Label>Scheme name</Label>
              <Input
                value={meta.scheme_name ?? ""}
                onChange={(e) => setMeta({ scheme_name: e.target.value })}
                placeholder="e.g. Flexi Benefits"
              />
            </div>
            <div className="w-40 space-y-1">
              <FieldLabel hint={`Applies to tiers without their own currency. Defaults to ${DEFAULT_CURRENCY}.`}>
                Default currency
              </FieldLabel>
              <Select
                value={meta.currency || DEFAULT_CURRENCY}
                onValueChange={(v) => setMeta({ currency: v })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CURRENCY_OPTIONS.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-40 space-y-1">
              <FieldLabel hint="Blank inherits the policy year period.">
                Effective start
              </FieldLabel>
              <Input
                type="date"
                value={meta.effective_start ?? ""}
                onChange={(e) =>
                  setMeta({ effective_start: e.target.value || null })
                }
              />
            </div>
            <div className="w-40 space-y-1">
              <Label>Effective end</Label>
              <Input
                type="date"
                value={meta.effective_end ?? ""}
                min={meta.effective_start || undefined}
                onChange={(e) =>
                  setMeta({ effective_end: e.target.value || null })
                }
              />
            </div>
            <div className="space-y-1">
              <FieldLabel hint="Extracted amounts are GST-exclusive; when on, flex price tags gross up by this rate (default 9%). A product's own GST setting under Configuration takes precedence.">
                GST
              </FieldLabel>
              <div className="flex h-9 items-center gap-3 whitespace-nowrap">
                <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
                  <Checkbox
                    checked={Boolean(meta.gst_included)}
                    onCheckedChange={(v) => setMeta({ gst_included: v === true })}
                  />
                  Include in price tags
                </label>
                {Boolean(meta.gst_included) && (
                  <div className="flex items-center gap-1.5">
                    <Input
                      type="number"
                      min={0}
                      max={100}
                      step={0.1}
                      className="h-8 w-16"
                      value={meta.gst_rate ?? ""}
                      onChange={(e) =>
                        setMeta({ gst_rate: numOrNull(e.target.value) })
                      }
                      placeholder="9"
                      aria-label="GST rate (%)"
                    />
                    <span className="text-sm text-muted-foreground">%</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Scheme-wide dependant age caps — the default eligibility window fed to
              pricing; a product's Flex-pricing entry can override per product. */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-3">
            <FieldLabel hint="Dependants past these ages (age next-birthday) are not covered and draw no flex. Scheme-wide default; a product's Flex-pricing entry overrides it. Blank inherits the platform default (spouse 70, child 25).">
              Dependant age limit
            </FieldLabel>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Spouse max</span>
              <Input
                type="number"
                min={0}
                className="w-20"
                value={depMax("spouse")}
                onChange={(e) => setDepMax("spouse", e.target.value)}
                placeholder="70"
                aria-label="Spouse maximum age"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Child max</span>
              <Input
                type="number"
                min={0}
                className="w-20"
                value={depMax("child")}
                onChange={(e) => setDepMax("child", e.target.value)}
                placeholder="25"
                aria-label="Child maximum age"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tiers */}
      <div ref={tiersRef} className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="font-medium text-foreground">
            Eligibility tiers ({body.tiers.length})
          </div>
          <div className="flex items-center gap-2">
            {body.tiers.length > 0 && (vocab?.employees_total ?? 0) > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={onSuggest}
                disabled={busy}
                title="Fill unreconciled tiers' match sets from the current roster"
              >
                <Sparkles className="size-4" /> Suggest from roster
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={addTier}>
              <Plus className="size-4" /> Add tier
            </Button>
          </div>
        </div>
        {body.tiers.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No tiers yet. Add one, or upload a document to extract them.
          </p>
        ) : (
          <Tabs value={activeValue} onValueChange={setActiveTier}>
            <TabsList className="flex-wrap h-auto gap-x-4 gap-y-1">
              {body.tiers.map((tier, i) => {
                const hc = headcountByName.get(tierKey(tier.name));
                const needsReview = flexTierReview(tier, hc).needsReview;
                return (
                  <TabsTrigger key={i} value={String(i)}>
                    <span className="max-w-[13rem] truncate">
                      {tier.name || `Tier ${i + 1}`}
                    </span>
                    {hc && (
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {hc.eligible.toLocaleString()}
                      </span>
                    )}
                    {needsReview && (
                      <span
                        className="ml-1.5 size-1.5 shrink-0 rounded-full bg-warn"
                        title="Needs review"
                        aria-label="Needs review"
                      />
                    )}
                  </TabsTrigger>
                );
              })}
            </TabsList>
            {body.tiers.map((tier, i) => {
              const hc = headcountByName.get(tierKey(tier.name));
              return (
                <TabsContent key={i} value={String(i)}>
                  <FlexTierEditor
                    tier={tier}
                    index={i}
                    headcount={hc}
                    currency={meta.currency ?? ""}
                    designations={vocab?.designations ?? []}
                    grades={vocab?.grades ?? []}
                    onChange={(t) => setTier(i, t)}
                    onRemove={() => removeTier(i)}
                    onSave={onSave}
                    saving={busy}
                    dirty={dirty}
                  />
                </TabsContent>
              );
            })}
          </Tabs>
        )}
      </div>

      {/* Validation summary + actions (end of all editable fields) */}
      {errors.length > 0 && (
        <div className="rounded-lg border border-border bg-warn-soft/40 p-3 text-sm">
          <div className="flex items-center gap-2 font-medium text-foreground">
            <AlertTriangle className="size-4 text-warn" />
            {errors.length} issue{errors.length === 1 ? "" : "s"} to resolve before
            confirming
          </div>
          <ul className="mt-1.5 list-disc pl-6 text-muted-foreground space-y-0.5">
            {errors.slice(0, 8).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
            {errors.length > 8 && <li>…and {errors.length - 8} more</li>}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 flex-wrap border-t border-border pt-4">
        <Button variant="outline" onClick={onSave} disabled={busy || !dirty}>
          <Save className="size-4" /> Save draft
        </Button>
        {scheme.status === "confirmed" && (
          <Button variant="outline" onClick={onAssign} disabled={busy}>
            <Wallet className="size-4" /> Re-assign wallets
          </Button>
        )}
        <Button onClick={onConfirm} disabled={busy || errors.length > 0}>
          <CheckCircle2 className="size-4" /> Confirm
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setConfirmDiscard(true)}
          disabled={busy}
          aria-label="Discard scheme"
        >
          <Trash2 className="size-4 text-error" />
        </Button>
      </div>

      <AlertDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        title="Discard flex scheme?"
        description="This removes the extracted/edited scheme for this policy year. You can re-upload a document to start again."
        confirmLabel="Discard"
        confirmVariant="destructive"
        onConfirm={onDiscard}
        loading={discard.isPending}
      />

      <AlertDialog
        open={unmatchedWarn !== null}
        onOpenChange={(open) => !open && setUnmatchedWarn(null)}
        title="Some employees would get no wallet"
        description={
          `${(unmatchedWarn ?? 0).toLocaleString()} active employee(s) match no ` +
          "eligibility tier, so they'll receive no flex wallet. Widen a tier's " +
          "match sets or add a catch-all tier — or confirm anyway."
        }
        confirmLabel="Confirm anyway"
        onConfirm={() => runConfirm(true)}
        loading={confirm.isPending}
      />
    </div>
  );
}
