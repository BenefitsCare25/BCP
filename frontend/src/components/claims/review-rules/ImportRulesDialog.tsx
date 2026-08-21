/** Duplicate claim-type rule setups from another company.
 *
 * Two steps in one dialog: pick a source company, then tick which of its
 * compatible configured claim types to copy. The backend intersects the
 * current claim-type catalogues of both companies, and each import lands on
 * that matching destination — creating it, or overwriting an existing custom
 * setup (marked).
 *
 * The company list comes from the BACKEND (`/claim-review-configs/sources`),
 * not from `me.accessible_clients`: for a system_admin that list spans every
 * broker firm, while an import may only read within the active client's firm
 * (another firm's rows live in another Postgres schema). Deriving it here
 * would offer companies the import is guaranteed to reject.
 */
import { useMemo, useState } from "react";
import { ArrowLeft, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  useClaimReviewConfigs,
  useImportReviewConfigs,
  useImportSourceCompanies,
  useSourceReviewConfigs,
} from "@/api/claims";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";

export function ImportRulesDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const companiesQuery = useImportSourceCompanies(open);
  const companies = companiesQuery.data ?? [];
  const source = useSourceReviewConfigs(open ? sourceId : null);
  const own = useClaimReviewConfigs();
  const importConfigs = useImportReviewConfigs();

  // Claim types already customized HERE — an import overwrites them. Both
  // sides carry the server's own join key (see `ClaimReviewConfig.key`); a
  // locally derived one could disagree with the UPSERT the import performs.
  const ownKeys = useMemo(
    () => new Set((own.data ?? []).map((c) => c.key)),
    [own.data],
  );
  const ownByKey = useMemo(
    () => new Map((own.data ?? []).map((config) => [config.key, config])),
    [own.data],
  );

  const close = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setSourceId(null);
      setPicked(new Set());
    }
  };

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };

  const runImport = () => {
    if (!sourceId || picked.size === 0) return;
    const selected = (source.data ?? []).filter((config) => picked.has(config.id));
    const targetVersions = Object.fromEntries(
      selected.flatMap((config) => {
        const current = ownByKey.get(config.key);
        return current ? [[config.key, current.updated_at]] : [];
      }),
    );
    importConfigs.mutate(
      {
        source_client_id: sourceId,
        config_ids: [...picked],
        target_versions: targetVersions,
      },
      {
        onSuccess: (r) => {
          toast.success(
            `Imported ${r.imported.length} claim type${r.imported.length === 1 ? "" : "s"}`,
          );
          close(false);
        },
        onError: (e) => toast.error(formatError(e)),
      },
    );
  };

  const sourceName =
    companies.find((c) => c.id === sourceId)?.name ?? "another company";
  // Drives the pinned footer: it only applies once a source is chosen and that
  // source actually has setups to copy.
  const canImport =
    sourceId !== null && !source.isLoading && (source.data ?? []).length > 0;

  return (
    <Sheet open={open} onOpenChange={close}>
      <SheetContent className="sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Duplicate rules from another company</SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-4">
          {sourceId === null ? (
            <>
              <p className="text-sm text-muted-foreground">
                Pick a source company. Only rule setups for claim types
                currently available in both companies can be duplicated.
              </p>
              {companiesQuery.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : companiesQuery.isError ? (
                <p className="text-sm text-error">
                  {formatError(companiesQuery.error)}
                </p>
              ) : companies.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  You have no other companies to copy from.
                </p>
              ) : (
                <div className="space-y-2">
                  {companies.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      disabled={c.configured_count === 0}
                      className="flex w-full items-center gap-3 rounded-md border border-border px-3 py-2.5 text-left text-sm text-foreground transition-colors hover:bg-muted focus-ring disabled:opacity-50 disabled:hover:bg-transparent"
                      onClick={() => setSourceId(c.id)}
                    >
                      <span className="font-medium">{c.name}</span>
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        {c.configured_count === 0
                          ? "no compatible setups"
                          : `${c.configured_count} compatible setup${c.configured_count === 1 ? "" : "s"}`}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => {
                  setSourceId(null);
                  setPicked(new Set());
                }}
              >
                <ArrowLeft className="size-3.5" /> Choose a different company
              </button>
              {source.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : source.isError ? (
                <p className="text-sm text-error">{formatError(source.error)}</p>
              ) : (source.data ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {sourceName} has no customized rule setups for claim types
                  currently available in both companies.
                </p>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">
                    Select the claim types to copy from {sourceName}. Each one
                    lands on the matching claim type of this company. Hospital
                    setups are separated by government and private scope.
                  </p>
                  {/* The label and its summary stack: side by side they ran
                      together as one sentence at small widths. */}
                  <div className="space-y-2">
                    {(source.data ?? []).map((cfg) => {
                      const overwrites = ownKeys.has(cfg.key);
                      return (
                        <label
                          key={cfg.id}
                          className="flex items-center gap-3 rounded-md border border-border px-3 py-2.5 text-sm transition-colors hover:bg-muted/50"
                        >
                          <Checkbox
                            checked={picked.has(cfg.id)}
                            onCheckedChange={() => toggle(cfg.id)}
                          />
                          <span className="min-w-0">
                            <span className="block font-medium text-foreground">
                              {cfg.display_label}
                            </span>
                            <span className="block text-xs text-muted-foreground">
                              {cfg.group_label && (
                                <>
                                  <span className="font-medium text-foreground">
                                    {cfg.group_label}
                                  </span>
                                  <span aria-hidden> · </span>
                                </>
                              )}
                              {cfg.field_map_count} mappings · {cfg.rule_count} rules
                              {cfg.required_document_count > 0 &&
                                ` · ${cfg.required_document_count} required docs`}
                            </span>
                          </span>
                          <span className="ml-auto flex shrink-0 items-center gap-1.5">
                            <Badge variant="outline">
                              {cfg.claim_kind === "flex" ? "Flex" : cfg.claim_key}
                            </Badge>
                            {overwrites && (
                              <Badge variant="warn">will overwrite</Badge>
                            )}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </SheetBody>

        {canImport && (
          <SheetFooter>
            <Button type="button" variant="ghost" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={picked.size === 0 || importConfigs.isPending}
              onClick={runImport}
            >
              {importConfigs.isPending && (
                <Loader2 className="size-3.5 animate-spin" />
              )}
              <span className={importConfigs.isPending ? "ml-1.5" : undefined}>
                Import {picked.size || ""} selected
              </span>
            </Button>
          </SheetFooter>
        )}
      </SheetContent>
    </Sheet>
  );
}
