/** Claim review rules tab — the per-claim-type AI review rule setup.
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
import { Pencil, RefreshCw, RotateCcw, Search } from "lucide-react";
import { toast } from "sonner";
import {
  useClaimReviewConfigs,
  useDeleteClaimReviewConfig,
  useReviewScopeOptions,
  type ClaimReviewConfig,
  type ClaimReviewConfigInput,
  type ReviewClaimScope,
  type ReviewClaimType,
} from "@/api/claims";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { SectionLabel } from "@/components/ui/section-label";
import { Skeleton } from "@/components/ui/skeleton";
import { NoCurrentYearNotice } from "@/components/shell/CurrentYearBanner";
import { Input } from "@/components/ui/input";
import { formatError } from "@/lib/errors";
import {
  ReviewConfigEditor,
  type EditorTarget,
  type ReviewDuplicateSource,
} from "./ReviewConfigEditor";

function summarize(cfg: ClaimReviewConfig): string {
  return [
    `${cfg.field_maps.length} mappings`,
    `${cfg.ai_rules.length} rules`,
  ].join(" · ");
}

function TypeRow({
  label,
  subLabel,
  config,
  inheritedConfig = null,
  nestingLevel = 0,
  onEdit,
  onRevert,
}: {
  label: string;
  subLabel?: string;
  config: ClaimReviewConfig | null;
  inheritedConfig?: ClaimReviewConfig | null;
  nestingLevel?: 0 | 1 | 2;
  onEdit: () => void;
  onRevert: () => void;
}) {
  const effective = config?.enabled ? config : inheritedConfig;
  const indentation =
    nestingLevel === 2 ? "pl-14 bg-muted/10" : nestingLevel === 1 ? "pl-9" : "";
  return (
    // A row on the section's divided rail, not its own card: a stack of
    // bordered cards inside a Card double-frames every claim type.
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3.5 transition-colors hover:bg-muted/40 ${indentation}`}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="text-xs text-muted-foreground">
          {effective
            ? summarize(effective)
            : (subLabel ?? "Built-in review rules")}
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
        ) : inheritedConfig ? (
          <Badge variant="outline">Inherited</Badge>
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
  title?: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={title ? "border-t border-border" : undefined}>
      {title && (
        <div className="space-y-1 bg-muted/40 px-5 py-2.5">
          <SectionLabel as="h3">{title}</SectionLabel>
          {note && <p className="max-w-prose text-xs text-subtle">{note}</p>}
        </div>
      )}
      <div className={`divide-y divide-border ${title ? "border-t border-border" : ""}`}>
        {children}
      </div>
    </section>
  );
}

function configurableScopes(type: ReviewClaimType): ReviewClaimScope[] {
  return type.scopes.filter((scope) => scope.configurable);
}

function showsChildScopes(type: ReviewClaimType): boolean {
  const scopes = configurableScopes(type);
  return (
    scopes.length > 1 ||
    scopes.some((scope) => scope.scope_code !== "standard")
  );
}

export function ReviewRuleSettings() {
  const options = useReviewScopeOptions();
  const configs = useClaimReviewConfigs();
  const del = useDeleteClaimReviewConfig();
  const [editing, setEditing] = useState<EditorTarget | null>(null);
  const [reverting, setReverting] = useState<ClaimReviewConfig | null>(null);
  const [query, setQuery] = useState("");

  // Keyed on the SERVER's `key` on both sides of the join — see
  // `ClaimReviewConfig.key`.
  const configByType = useMemo(() => {
    const map = new Map<string, ClaimReviewConfig>();
    for (const c of configs.data ?? []) map.set(c.key, c);
    return map;
  }, [configs.data]);

  const claimTypes = options.data?.claim_types ?? [];
  const needle = query.trim().toLowerCase();
  const visibleClaimTypes = useMemo(
    () =>
      needle
        ? claimTypes.filter((type) =>
            [
              type.display_label,
              ...type.scopes.map((scope) => scope.display_label),
            ].some((label) => label.toLowerCase().includes(needle)),
          )
        : claimTypes,
    [claimTypes, needle],
  );
  const duplicateSources = useMemo<ReviewDuplicateSource[]>(() => {
    const defaults = options.data?.default_config;
    if (!defaults) return [];
    const builtIn = {
      field_maps: defaults.field_maps,
      ai_rules: defaults.ai_rules,
    };
    const setupFrom = (config: ClaimReviewConfig | null) =>
      config?.enabled
        ? {
            field_maps: config.field_maps,
            ai_rules: config.ai_rules,
          }
        : null;
    const out: ReviewDuplicateSource[] = [];
    for (const type of claimTypes) {
      const productConfig = configByType.get(type.key) ?? null;
      const productSetup = setupFrom(productConfig) ?? builtIn;
      out.push({
        key: type.key,
        label: showsChildScopes(type)
          ? `${type.display_label} — product default`
          : type.display_label,
        setup: productSetup,
      });
      if (!showsChildScopes(type)) continue;
      for (const scope of configurableScopes(type)) {
        const exact = configByType.get(scope.key) ?? null;
        const inherited = scope.parent_key
          ? configByType.get(scope.parent_key) ?? null
          : null;
        out.push({
          key: scope.key,
          label: [type.display_label, scope.group_label, scope.display_label]
            .filter(Boolean)
            .join(" — "),
          setup:
            setupFrom(exact) ?? setupFrom(inherited) ?? productSetup,
        });
      }
    }
    return out;
  }, [claimTypes, configByType, options.data?.default_config]);
  const knownKeys = new Set(
    claimTypes.flatMap((t) => [t.key, ...t.scopes.map((scope) => scope.key)]),
  );
  // Customized setups with no matching claim type. Normally that means the
  // type is gone (product removed, flex category renamed) — inert, but they
  // must stay visible to edit or delete. With NO current year there is no
  // vocabulary at all, so EVERY setup lands here and "No longer active" would
  // be a lie; the heading and note switch accordingly.
  const hasCurrentYear = options.data?.has_current_year ?? true;
  const unmatched = (configs.data ?? []).filter(
    (c) =>
      !knownKeys.has(c.key) &&
      (!needle || c.display_label.toLowerCase().includes(needle)),
  );

  const openEditor = (
    t: RuleTarget,
    config: ClaimReviewConfig | null,
    inheritedConfig: ClaimReviewConfig | null = null,
  ) => {
    const defaults = options.data?.default_config;
    const source = config ?? inheritedConfig;
    const draft: ClaimReviewConfigInput = source
      ? {
          claim_kind: t.claim_kind,
          claim_key: t.claim_key,
          scope_code: t.scope_code,
          display_label: config?.display_label ?? t.display_label,
          enabled: config?.enabled ?? true,
          field_maps: source.field_maps.map((m) => ({ ...m })),
          ai_rules: source.ai_rules.map((r) => ({ ...r })),
          required_documents: [...source.required_documents],
        }
      : {
          claim_kind: t.claim_kind,
          claim_key: t.claim_key,
          scope_code: t.scope_code,
          display_label: t.display_label,
          enabled: true,
          field_maps: (defaults?.field_maps ?? []).map((m) => ({ ...m })),
          ai_rules: (defaults?.ai_rules ?? []).map((r) => ({ ...r })),
          required_documents: [...(defaults?.required_documents ?? [])],
        };
    setEditing({
      key: t.key,
      configId: config?.id ?? null,
      expectedUpdatedAt: config?.updated_at ?? null,
      draft,
    });
  };

  const openOrphanEditor = (config: ClaimReviewConfig) => {
    setEditing({
      key: config.key,
      configId: config.id,
      expectedUpdatedAt: config.updated_at,
      draft: {
        claim_kind: config.claim_kind,
        claim_key: config.claim_key,
        scope_code: config.scope_code,
        display_label: config.display_label,
        enabled: config.enabled,
        field_maps: config.field_maps.map((m) => ({ ...m })),
        ai_rules: config.ai_rules.map((r) => ({ ...r })),
        required_documents: [...config.required_documents],
      },
    });
  };

  const insured = visibleClaimTypes.filter((t) => t.claim_kind === "insured");
  const flex = visibleClaimTypes.filter((t) => t.claim_kind === "flex");
  const loading = options.isLoading || configs.isLoading;

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center gap-3 border-b border-border p-5">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                {claimTypes.length} claim types · {configs.data?.length ?? 0} custom setups
              </p>
              <p className="text-xs text-subtle">
                Filter the review contract by product or claim choice.
              </p>
            </div>
            <label className="relative w-full sm:w-72">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <span className="sr-only">Search review rules</span>
              <Input
                type="search"
                value={query}
                className="pl-9"
                placeholder="Search claim types"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </div>
          {loading ? (
            <div className="p-5">
              <Skeleton className="h-40 w-full" />
            </div>
          ) : options.isError || configs.isError ? (
            <div className="flex flex-col items-start gap-3 p-5">
              <p className="text-sm text-error">
                Couldn&apos;t load review rules. {formatError(options.error ?? configs.error)}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  void options.refetch();
                  void configs.refetch();
                }}
              >
                <RefreshCw className="size-4" /> Retry
              </Button>
            </div>
          ) : claimTypes.length === 0 && unmatched.length === 0 ? (
            <div className="p-5">
              {hasCurrentYear ? (
                <p className="text-sm text-muted-foreground">
                  No claim types yet — they appear once the current benefit year
                  has member-claimable products (or a flex scheme) configured.
                </p>
              ) : (
                <NoCurrentYearNotice />
              )}
            </div>
          ) : needle && visibleClaimTypes.length === 0 && unmatched.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">
              No claim types match your search.
            </p>
          ) : (
            <>
              {!hasCurrentYear && (
                <div className="p-5">
                  <NoCurrentYearNotice />
                </div>
              )}
              {insured.length > 0 && (
                <TypeSection>
                  {insured.map((t) => {
                    const cfg = configByType.get(t.key) ?? null;
                    const enabledParent = cfg?.enabled ? cfg : null;
                    const showChildren = showsChildScopes(t);
                    const scopes = configurableScopes(t);
                    const ungrouped = scopes.filter(
                      (scope) => !scope.group_code,
                    );
                    const groupCodes = Array.from(
                      new Set(
                        scopes
                          .map((scope) => scope.group_code)
                          .filter((code): code is string => Boolean(code)),
                      ),
                    );
                    const renderScope = (scope: ReviewClaimScope) => {
                      const child = configByType.get(scope.key) ?? null;
                      const parentConfig = scope.parent_key
                        ? configByType.get(scope.parent_key) ?? null
                        : null;
                      const inheritedConfig = parentConfig?.enabled
                        ? parentConfig
                        : enabledParent;
                      return (
                        <TypeRow
                          key={scope.key}
                          label={scope.display_label}
                          config={child}
                          inheritedConfig={inheritedConfig}
                          nestingLevel={scope.group_code ? 2 : 1}
                          onEdit={() =>
                            openEditor(
                              scopeTarget(t, scope),
                              child,
                              inheritedConfig,
                            )
                          }
                          onRevert={() => child && setReverting(child)}
                        />
                      );
                    };
                    return (
                      <div key={t.key} className="divide-y divide-border">
                        <TypeRow
                          label={showChildren ? `${t.display_label} default` : t.display_label}
                          subLabel={
                            showChildren
                              ? "Inherited by every claim choice below"
                              : undefined
                          }
                          config={cfg}
                          onEdit={() =>
                            openEditor({ ...t, scope_code: "*" }, cfg)
                          }
                          onRevert={() => cfg && setReverting(cfg)}
                        />
                        {showChildren && ungrouped.map(renderScope)}
                        {showChildren &&
                          groupCodes.map((groupCode) => {
                            const grouped = scopes.filter(
                              (scope) => scope.group_code === groupCode,
                            );
                            return (
                              <div key={groupCode}>
                                <div className="bg-muted/20 px-9 py-2.5">
                                  <SectionLabel as="h4">
                                    {grouped[0]?.group_label ?? groupCode}
                                  </SectionLabel>
                                </div>
                                <div className="divide-y divide-border border-t border-border">
                                  {grouped.map(renderScope)}
                                </div>
                              </div>
                            );
                          })}
                      </div>
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
                        onEdit={() => openEditor({ ...t, scope_code: "*" }, cfg)}
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
                      : "Claim types are read from the benefit year covering today. Review its dates and these match back to their claim types."
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

        <ReviewConfigEditor
          target={editing}
          portalFields={options.data?.portal_fields ?? []}
          duplicateSources={duplicateSources.filter(
            (source) => source.key !== editing?.key,
          )}
          onClose={() => setEditing(null)}
        />
        <AlertDialog
          open={reverting !== null}
          onOpenChange={(open) => !open && setReverting(null)}
          title={`Revert "${reverting?.display_label}" to the default rules?`}
          description="The custom field mappings and business rules are deleted. Reviews then inherit the product setup when one exists, or use the built-in defaults. Submission documents stay unchanged in Claim settings."
          confirmLabel="Revert to defaults"
          loading={del.isPending}
          onConfirm={() => {
            if (!reverting) return;
            del.mutate({ id: reverting.id, expected_updated_at: reverting.updated_at }, {
              onSuccess: () => setReverting(null),
              onError: (e) => toast.error(formatError(e)),
            });
          }}
        />
      </Card>
    </div>
  );
}

type RuleTarget = Pick<
  ReviewClaimType,
  "claim_kind" | "claim_key" | "key" | "display_label"
> & { scope_code: string };

function scopeTarget(type: ReviewClaimType, scope: ReviewClaimScope): RuleTarget {
  return {
    claim_kind: type.claim_kind,
    claim_key: type.claim_key,
    key: scope.key,
    scope_code: scope.scope_code,
    display_label: scope.display_label,
  };
}
