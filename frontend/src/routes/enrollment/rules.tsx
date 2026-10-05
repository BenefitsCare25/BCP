/** Enrollment → Pricing & rules: everything the benefit YEAR prices, in one
 * place — what each plan draws from a flex wallet, and what buying or selling
 * leave is worth. Periods come and go; these belong to the year.
 *
 * Price corrections remain available during an open period. Leave rules,
 * dependant participation and eligibility stay locked. */
import { useEffect, useRef, useState } from "react";
import { Loader2, Lock } from "lucide-react";
import { toast } from "sonner";
import {
  useEnrollmentWindows,
  useFlexPricing,
  useSaveEnrollmentPricingConfig,
} from "@/api/enrollment";
import { useFlexPricingEditor } from "@/components/enrollment/FlexPricingCard";
import { FlexProductList } from "@/components/enrollment/FlexProductList";
import { LeavePolicyCard } from "@/components/enrollment/LeavePolicyCard";
import { voluntaryRateIssues } from "@/components/enrollment/LifeVoluntaryPanel";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";
import { useSession } from "@/stores/session";

export function EnrollmentRulesPage({
  readOnly,
  section,
}: {
  readOnly: boolean;
  section?: string;
}) {
  const policyYearId = useSession((s) => s.currentPolicyYearId) ?? undefined;
  const { data: windows } = useEnrollmentWindows(policyYearId);
  const leaveRef = useRef<HTMLElement>(null);
  const openWindow = (windows ?? []).find((w) => w.status === "open");

  useEffect(() => {
    if (section === "leave") leaveRef.current?.scrollIntoView({ block: "start" });
  }, [section]);

  if (!policyYearId) {
    return <p className="text-sm text-muted-foreground">Select a benefit year first.</p>;
  }

  return (
    <div className="space-y-6">
      {openWindow && (
        <p className="flex items-start gap-2 rounded-lg bg-warn-soft/60 px-4 py-2.5 text-sm text-foreground">
          <Lock className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          <span>
            <strong>{openWindow.name}</strong> is open. You can supply missing
            prices or correct rates. Leave rules, dependant participation and
            eligibility stay locked. Previously priced submissions keep their
            saved amounts.
          </span>
        </p>
      )}
      <nav aria-label="Sections" className="flex gap-4 text-sm">
        <a href="#price-tags" className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
          Plan price tags
        </a>
        <a href="#leave-rules" className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
          Buying &amp; selling leave
        </a>
      </nav>
      <PriceTags
        policyYearId={policyYearId}
        editable={!readOnly && Boolean(windows)}
        rulesEditable={!openWindow && !readOnly && Boolean(windows)}
      />
      <section id="leave-rules" ref={leaveRef} className="scroll-mt-4">
        <LeavePolicyCard
          key={policyYearId}
          policyYearId={policyYearId}
          readOnly={Boolean(openWindow) || readOnly}
        />
      </section>
    </div>
  );
}

function PriceTags({
  policyYearId,
  editable,
  rulesEditable,
}: {
  policyYearId: string;
  editable: boolean;
  rulesEditable: boolean;
}) {
  const { data: flexPricing, isLoading } = useFlexPricing(policyYearId);
  const editor = useFlexPricingEditor(policyYearId);
  const save = useSaveEnrollmentPricingConfig(policyYearId);
  const [openEditor, setOpenEditor] = useState<Record<string, boolean>>({});
  const products = flexPricing?.products ?? [];
  // Incomplete recommendations stay visible as attention items; only an
  // explicit age-band override the broker edited must be complete to save.
  const invalidAge = products.filter(
    (product) =>
      product.tiers.some((tier) => tier.pricing_mode === "age_banded") &&
      editor.voluntaryRatesEdited(product) &&
      voluntaryRateIssues(editor.voluntaryRatesFor(product)).length > 0,
  );

  async function onSave() {
    if (invalidAge.length) {
      toast.error(
        `Review the age bands for ${invalidAge.map((p) => p.product_code).join(", ")}.`,
      );
      return;
    }
    try {
      await save.mutateAsync({ pricing: editor.pricing });
      editor.markSaved();
      toast.success("Price tags saved.");
    } catch (e) {
      toast.error(formatError(e));
    }
  }

  return (
    <section id="price-tags" className="scroll-mt-4 rounded-xl border border-border bg-card">
      <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 rounded-t-xl border-b border-border bg-card/95 px-5 py-4 backdrop-blur-sm">
        <div>
          <h2 className="text-base font-semibold text-foreground">Plan price tags</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            What each plan draws from a member&apos;s flex wallet — separate from the
            insurer premium. Values come from the placement slip; change only the
            ones that are wrong.
          </p>
        </div>
        {editor.dirty && editable && (
          <Button onClick={() => void onSave()} disabled={save.isPending || invalidAge.length > 0}>
            {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Save price tags
          </Button>
        )}
      </header>
      <div className="px-5 py-4">
        {isLoading ? (
          <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading…
          </p>
        ) : (
          <FlexProductList
            products={products}
            pricing={editor.pricing}
            editor={editor}
            editable={editable}
            rulesEditable={rulesEditable}
            openEditor={openEditor}
            onToggleEditor={(pid) => setOpenEditor((s) => ({ ...s, [pid]: !s[pid] }))}
            emptyHint={
              <p className="text-sm text-muted-foreground">
                No flex-priced products this year yet. Products appear once the
                placement slip is parsed and the flex scheme is confirmed.
              </p>
            }
          />
        )}
      </div>
    </section>
  );
}
