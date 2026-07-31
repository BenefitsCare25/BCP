/** AI extraction tab — the per-claim-type review rule setup.
 *
 * Lists the company's claim types (insured products + flex benefit categories
 * of the current benefit year, from the backend options endpoint). A type with
 * no custom setup shows a "Default" badge and runs on the built-in rules;
 * "Customize" opens the editor prefilled with those defaults. Setups whose
 * claim type no longer exists (product removed, flex category renamed) are
 * still listed under "No longer active" — they'd otherwise be invisible.
 *
 * The vocabulary comes from the CURRENT benefit year alone, so with no year
 * flagged current this page is empty for a reason that has nothing to do with
 * the claim rules — and everything to do with the member portal being dark.
 * `has_current_year` separates the two cases; never show the generic "no claim
 * types yet" copy for it, which reads as false to a broker looking at a
 * configured product list.
 */
import { useMemo, useState } from "react";
import { Copy, Pencil, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import {
  useClaimReviewConfigs,
  useDeleteClaimReviewConfig,
  useReviewScopeOptions,
  type ClaimReviewConfig,
  type ClaimReviewConfigInput,
  type ReviewClaimType,
} from "@/api/claims";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SectionLabel } from "@/components/ui/section-label";
import { Skeleton } from "@/components/ui/skeleton";
import { NoCurrentYearNotice } from "@/components/shell/CurrentYearBanner";
import { formatError } from "@/lib/errors";
import { ImportRulesDialog } from "./ImportRulesDialog";
import { ReviewConfigEditor, type EditorTarget } from "./ReviewConfigEditor";

function summarize(cfg: ClaimReviewConfig): string {
  const parts = [
    `${cfg.field_maps.length} mappings`,
    `${cfg.ai_rules.length} rules`,
  ];
  if (cfg.required_documents.length > 0) {
    parts.push(`+${cfg.required_documents.length} required docs`);
  }
  return parts.join(" · ");
}

function TypeRow({
  label,
  subLabel,
  config,
  onEdit,
  onRevert,
}: {
  label: string;
  subLabel?: string;
  config: ClaimReviewConfig | null;
  onEdit: () => void;
  onRevert: () => void;
}) {
  return (
    // A row on the section's divided rail, not its own card: a stack of
    // bordered cards inside a Card double-frames every claim type.
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3.5 transition-colors hover:bg-muted/40">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="text-xs text-muted-foreground">
          {config ? summarize(config) : (subLabel ?? "Built-in review rules")}
        </p>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {config && config.enabled && config.ai_rules.length === 0 && (
          // Legitimate but consequential: the built-in fraud rules are off for
          // this claim type. Surface it in the list, not just in the editor.
          <Badge variant="warn" title="No business rules — field comparisons only">
            No rules
          </Badge>
        )}
        {config ? (
          <Badge variant={config.enabled ? "info" : "outline"}>
            {config.enabled ? "Custom" : "Custom (off)"}
          </Badge>
        ) : (
          <Badge variant="outline">Default</Badge>
        )}
        {config && (
          <Button type="button" variant="ghost" size="sm" onClick={onRevert}>
            <RotateCcw className="size-3.5" />
            <span className="ml-1">Revert</span>
          </Button>
        )}
        <Button type="button" variant="outline" size="sm" onClick={onEdit}>
          <Pencil className="size-3.5" />
          <span className="ml-1">{config ? "Edit" : "Customize"}</span>
        </Button>
      </div>
    </div>
  );
}

/** A named group of claim types: a banded header over its own divided rail, so
 * the three groups read as groups without each row needing its own frame. */
function TypeSection({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border">
      <div className="space-y-1 bg-muted/40 px-5 py-2.5">
        <SectionLabel as="h3">{title}</SectionLabel>
        {note && <p className="max-w-prose text-xs text-subtle">{note}</p>}
      </div>
      <div className="divide-y divide-border border-t border-border">
        {children}
      </div>
    </section>
  );
}

export function ReviewRuleSettings() {
  const options = useReviewScopeOptions();
  const configs = useClaimReviewConfigs();
  const del = useDeleteClaimReviewConfig();
  const [editing, setEditing] = useState<EditorTarget | null>(null);
  const [reverting, setReverting] = useState<ClaimReviewConfig | null>(null);
  const [importOpen, setImportOpen] = useState(false);

  // Keyed on the SERVER's `key` on both sides of the join — see
  // `ClaimReviewConfig.key`.
  const configByType = useMemo(() => {
    const map = new Map<string, ClaimReviewConfig>();
    for (const c of configs.data ?? []) map.set(c.key, c);
    return map;
  }, [configs.data]);

  const claimTypes = options.data?.claim_types ?? [];
  const knownKeys = new Set(claimTypes.map((t) => t.key));
  // Customized setups with no matching claim type. Normally that means the
  // type is gone (product removed, flex category renamed) — inert, but they
  // must stay visible to edit or delete. With NO current year there is no
  // vocabulary at all, so EVERY setup lands here and "No longer active" would
  // be a lie; the heading and note switch accordingly.
  const hasCurrentYear = options.data?.has_current_year ?? true;
  const unmatched = (configs.data ?? []).filter((c) => !knownKeys.has(c.key));

  const openEditor = (t: ReviewClaimType, config: ClaimReviewConfig | null) => {
    const defaults = options.data?.default_config;
    const draft: ClaimReviewConfigInput = config
      ? {
          claim_kind: config.claim_kind,
          claim_key: config.claim_key,
          display_label: config.display_label,
          enabled: config.enabled,
          field_maps: config.field_maps.map((m) => ({ ...m })),
          ai_rules: config.ai_rules.map((r) => ({ ...r })),
          required_documents: [...config.required_documents],
        }
      : {
          claim_kind: t.claim_kind,
          claim_key: t.claim_key,
          display_label: t.display_label,
          enabled: true,
          field_maps: (defaults?.field_maps ?? []).map((m) => ({ ...m })),
          ai_rules: (defaults?.ai_rules ?? []).map((r) => ({ ...r })),
          required_documents: [...(defaults?.required_documents ?? [])],
        };
    setEditing({ configId: config?.id ?? null, draft });
  };

  const openOrphanEditor = (config: ClaimReviewConfig) => {
    setEditing({
      configId: config.id,
      draft: {
        claim_kind: config.claim_kind,
        claim_key: config.claim_key,
        display_label: config.display_label,
        enabled: config.enabled,
        field_maps: config.field_maps.map((m) => ({ ...m })),
        ai_rules: config.ai_rules.map((r) => ({ ...r })),
        required_documents: [...config.required_documents],
      },
    });
  };

  const insured = claimTypes.filter((t) => t.claim_kind === "insured");
  const flex = claimTypes.filter((t) => t.claim_kind === "flex");
  const loading = options.isLoading || configs.isLoading;

  return (
    <Card>
      <CardHeader className="pb-5">
        {/* basis-80 + shrink-0 keeps the action on the title's line; without it
            the description absorbs the row and drops the button below. */}
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="min-w-0 flex-1 basis-80 space-y-1">
            <CardTitle>AI review rules by claim type</CardTitle>
            <CardDescription className="max-w-prose">
              What the AI checks when it reviews a submitted claim — field
              comparisons, business rules and required documents, configurable
              per claim type. Types without a custom setup use the built-in
              defaults.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => setImportOpen(true)}
          >
            <Copy className="size-3.5" />
            <span className="ml-1.5">Duplicate from another company</span>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {loading ? (
          <div className="px-5 pb-5">
            <Skeleton className="h-40 w-full" />
          </div>
        ) : options.isError ? (
          <p className="px-5 pb-5 text-sm text-error">
            {formatError(options.error)}
          </p>
        ) : claimTypes.length === 0 && unmatched.length === 0 ? (
          <div className="px-5 pb-5">
            {hasCurrentYear ? (
              <p className="text-sm text-muted-foreground">
                No claim types yet — they appear once the current benefit year
                has member-claimable products (or a flex scheme) configured.
              </p>
            ) : (
              <NoCurrentYearNotice />
            )}
          </div>
        ) : (
          <>
            {!hasCurrentYear && (
              <div className="px-5 pb-5">
                <NoCurrentYearNotice />
              </div>
            )}
            {insured.length > 0 && (
              <TypeSection title="Insurance products">
                {insured.map((t) => {
                  const cfg = configByType.get(t.key) ?? null;
                  return (
                    <TypeRow
                      key={t.key}
                      label={t.display_label}
                      config={cfg}
                      onEdit={() => openEditor(t, cfg)}
                      onRevert={() => cfg && setReverting(cfg)}
                    />
                  );
                })}
              </TypeSection>
            )}
            {flex.length > 0 && (
              <TypeSection title="Flexible benefits">
                {flex.map((t) => {
                  const cfg = configByType.get(t.key) ?? null;
                  return (
                    <TypeRow
                      key={t.key}
                      label={t.display_label}
                      config={cfg}
                      onEdit={() => openEditor(t, cfg)}
                      onRevert={() => cfg && setReverting(cfg)}
                    />
                  );
                })}
              </TypeSection>
            )}
            {unmatched.length > 0 && (
              <TypeSection
                title={hasCurrentYear ? "No longer active" : "Configured setups"}
                note={
                  hasCurrentYear
                    ? "These setups reference a claim type not in the current benefit year (product removed or flex category renamed) — reviews use the defaults until the type returns."
                    : "Claim types are read from the current benefit year. Set one as current and these match back to their claim types."
                }
              >
                {unmatched.map((cfg) => (
                  <TypeRow
                    key={cfg.id}
                    label={cfg.display_label}
                    subLabel={summarize(cfg)}
                    config={cfg}
                    onEdit={() => openOrphanEditor(cfg)}
                    onRevert={() => setReverting(cfg)}
                  />
                ))}
              </TypeSection>
            )}
          </>
        )}
      </CardContent>

      <ReviewConfigEditor target={editing} onClose={() => setEditing(null)} />
      <ImportRulesDialog open={importOpen} onOpenChange={setImportOpen} />
      <AlertDialog
        open={reverting !== null}
        onOpenChange={(open) => !open && setReverting(null)}
        title={`Revert "${reverting?.display_label}" to the default rules?`}
        description="The custom field mappings, business rules and required documents for this claim type are deleted, and its AI reviews use the built-in defaults again."
        confirmLabel="Revert to defaults"
        loading={del.isPending}
        onConfirm={() => {
          if (!reverting) return;
          del.mutate(reverting.id, {
            onSuccess: () => setReverting(null),
            onError: (e) => toast.error(formatError(e)),
          });
        }}
      />
    </Card>
  );
}
