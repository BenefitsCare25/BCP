import { useEffect, useState } from "react";
import { Copy, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateClaimReviewConfig,
  usePreviewReviewPrompt,
  useUpdateClaimReviewConfig,
  type ClaimReviewConfigInput,
  type ReviewAIRule,
  type ReviewFieldMap,
  type ReviewMatchMode,
  type ReviewSeverity,
} from "@/api/claims";
import { Button } from "@/components/ui/button";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError, isStaleConfigurationError } from "@/lib/errors";
import { ReviewConfigEditorSection as EditorSection } from "./ReviewConfigEditorSection";
import { ReviewPromptPreview } from "./ReviewPromptPreview";
import { ConfigurationHistory } from "../ConfigurationHistory";
import {
  MAX_AI_RULES,
  MAX_FIELD_MAPS,
  prepareReviewConfigDraft,
  type EditorTarget,
} from "./reviewConfigDraft";
export type { EditorTarget } from "./reviewConfigDraft";
export interface ReviewDuplicateSource {
  key: string;
  label: string;
  setup: Pick<
    ClaimReviewConfigInput,
    "field_maps" | "ai_rules"
  >;
}

function isReviewFieldMap(value: unknown): value is ReviewFieldMap {
  if (!value || typeof value !== "object") return false;
  const mapping = value as Partial<ReviewFieldMap>;
  return (
    typeof mapping.portal_field === "string" &&
    typeof mapping.document_field === "string" &&
    ["fuzzy", "exact", "numeric"].includes(mapping.mode ?? "") &&
    typeof mapping.verify_with_vision === "boolean" &&
    typeof mapping.require_evidence === "boolean" &&
    (mapping.tolerance == null ||
      (typeof mapping.tolerance === "number" &&
        Number.isFinite(mapping.tolerance) &&
        mapping.tolerance >= 0))
  );
}

function isReviewAIRule(value: unknown): value is ReviewAIRule {
  if (!value || typeof value !== "object") return false;
  const rule = value as Partial<ReviewAIRule>;
  return (
    typeof rule.rule === "string" &&
    typeof rule.category === "string" &&
    ["critical", "warning", "info"].includes(rule.severity ?? "") &&
    (rule.id == null || typeof rule.id === "string")
  );
}

export function ReviewConfigEditor({
  target,
  portalFields,
  duplicateSources,
  onClose,
}: {
  target: EditorTarget | null;
  portalFields: string[];
  duplicateSources: ReviewDuplicateSource[];
  onClose: () => void;
}) {
  const create = useCreateClaimReviewConfig();
  const update = useUpdateClaimReviewConfig();
  const preview = usePreviewReviewPrompt();
  const [draft, setDraft] = useState<ClaimReviewConfigInput | null>(null);
  const [promptText, setPromptText] = useState<string | null>(null);
  const [duplicateSourceKey, setDuplicateSourceKey] = useState("");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  useEffect(() => {
    setDraft(target ? target.draft : null);
    setPromptText(null);
    setDuplicateSourceKey("");
    setConfirmDiscard(false);
  }, [target]);
  if (!target || !draft) return null;
  const saving = create.isPending || update.isPending;
  const dirty = JSON.stringify(draft) !== JSON.stringify(target.draft);
  const requestClose = () => {
    if (saving) return;
    if (dirty) setConfirmDiscard(true);
    else onClose();
  };

  const patch = (p: Partial<ClaimReviewConfigInput>) => {
    setDraft({ ...draft, ...p });
    setPromptText(null); // stale against the edited draft
  };
  const patchMap = (i: number, p: Partial<ReviewFieldMap>) =>
    patch({
      field_maps: draft.field_maps.map((m, j) => (j === i ? { ...m, ...p } : m)),
    });
  const patchRule = (i: number, p: Partial<ReviewAIRule>) =>
    patch({
      ai_rules: draft.ai_rules.map((r, j) => (j === i ? { ...r, ...p } : r)),
    });

  const save = () => {
    const prepared = prepareReviewConfigDraft(draft);
    if (!prepared.ok) {
      toast.error(prepared.error);
      return;
    }
    const done = {
      onSuccess: onClose,
      onError: (error: unknown) => {
        toast.error(formatError(error));
        if (isStaleConfigurationError(error)) onClose();
      },
    };
    if (target.configId) {
      if (!target.expectedUpdatedAt) {
        toast.error("Reload this setup before saving it.");
        return;
      }
      update.mutate(
        {
          id: target.configId,
          expected_updated_at: target.expectedUpdatedAt,
          ...prepared.body,
        },
        done,
      );
    } else {
      create.mutate(prepared.body, done);
    }
  };

  const showPreview = () => {
    const prepared = prepareReviewConfigDraft(draft);
    if (!prepared.ok) {
      toast.error(prepared.error);
      return;
    }
    preview.mutate(prepared.body, {
      onSuccess: (r) => setPromptText(r.prompt),
      onError: (e) => toast.error(formatError(e)),
    });
  };

  return (
    <>
      <Sheet open onOpenChange={(open) => !open && requestClose()}>
        <SheetContent className="sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{draft.display_label} — review rules</SheetTitle>
          <SheetDescription>
            Changes apply to reviews queued after you save. Queued and completed
            reviews retain the exact ruleset snapshot they started with.
            {target.expectedUpdatedAt && (
              <> Last saved {new Date(target.expectedUpdatedAt).toLocaleString()}.</>
            )}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-7">
          <label className="flex items-center gap-2.5 rounded-md border border-border bg-muted/40 px-3.5 py-3 text-sm font-medium text-foreground">
            <Switch
              checked={draft.enabled}
              onCheckedChange={(v) => patch({ enabled: v })}
            />
            Use this setup
            <InfoHint>
              Switched off, this claim choice inherits its product setup when
              available, then falls back to the built-in rules. Your saved
              configuration is retained.
            </InfoHint>
          </label>

          <div className="space-y-2 rounded-md border border-border px-3.5 py-3">
            <div>
              <p className="text-sm font-medium text-foreground">
                Duplicate another claim type&apos;s setup
              </p>
              <p className="text-xs text-subtle">
                Copies its effective mappings and rules into this editor.
                Submission documents remain owned by Doc settings.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <NativeSelect
                value={duplicateSourceKey}
                className="min-w-64 flex-1"
                aria-label="Claim type setup to duplicate"
                onChange={(event) => setDuplicateSourceKey(event.target.value)}
              >
                <option value="">Select another claim type</option>
                {duplicateSources.map((source) => (
                  <option key={source.key} value={source.key}>
                    {source.label}
                  </option>
                ))}
              </NativeSelect>
              <Button
                type="button"
                variant="outline"
                disabled={!duplicateSourceKey}
                onClick={() => {
                  const source = duplicateSources.find(
                    (candidate) => candidate.key === duplicateSourceKey,
                  );
                  if (!source) return;
                  patch({
                    enabled: true,
                    field_maps: source.setup.field_maps.map((mapping) => ({
                      ...mapping,
                    })),
                    ai_rules: source.setup.ai_rules.map((rule) => ({ ...rule })),
                  });
                  toast.success(`Copied setup from ${source.label}`);
                }}
              >
                <Copy className="size-3.5" />
                <span className="ml-1.5">Duplicate setup</span>
              </Button>
            </div>
          </div>

          <EditorSection
            title="Field mappings"
            hint={
              <>
                Which claim-form field is checked against which document field,
                and how strictly. <strong>Vision re-check</strong> spends an
                extra AI image pass when the text comparison disagrees (a cost
                control). <strong>Require evidence</strong> flags the claim when
                it states a value that no document substantiates — keep it on
                for money, dates and the provider even if you switch vision off.
              </>
            }
          >
            <div className="space-y-2">
              {draft.field_maps.map((m, i) => (
                <div
                  key={i}
                  className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5"
                >
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto] items-center gap-2">
                    <NativeSelect
                      value={m.portal_field}
                      className="h-8 bg-card text-xs"
                      aria-label="Claim field"
                      onChange={(e) => patchMap(i, { portal_field: e.target.value })}
                    >
                      <option value="">Select claim field</option>
                      {m.portal_field && !portalFields.includes(m.portal_field) && (
                        <option value={m.portal_field}>{m.portal_field} (unsupported)</option>
                      )}
                      {portalFields.map((field) => (
                        <option key={field} value={field}>
                          {field.replaceAll("_", " ")}
                        </option>
                      ))}
                    </NativeSelect>
                    <span aria-hidden className="text-sm text-muted-foreground">
                      ↔
                    </span>
                    <Input
                      value={m.document_field}
                      placeholder="Document field (e.g. Total Amount)"
                      maxLength={128}
                      className="h-8 bg-card text-xs"
                      aria-label="Document field"
                      onChange={(e) => patchMap(i, { document_field: e.target.value })}
                    />
                    <button
                      type="button"
                      className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-card hover:text-error"
                      aria-label={`Remove mapping for ${m.portal_field || "this field"}`}
                      onClick={() =>
                        patch({ field_maps: draft.field_maps.filter((_, j) => j !== i) })
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pl-0.5">
                    <NativeSelect
                      value={m.mode}
                      aria-label="Match mode"
                      className="h-7 bg-card text-xs"
                      onChange={(e) =>
                        patchMap(i, {
                          mode: e.target.value as ReviewMatchMode,
                          tolerance:
                            e.target.value === "numeric" ? (m.tolerance ?? 0.01) : null,
                        })
                      }
                    >
                      <option value="fuzzy">Fuzzy match</option>
                      <option value="exact">Exact match</option>
                      <option value="numeric">Numeric match</option>
                    </NativeSelect>
                    {m.mode === "numeric" && (
                      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        Tolerance
                        <Input
                          type="number"
                          min={0}
                          step="0.01"
                          value={m.tolerance ?? 0}
                          className="h-7 w-20 bg-card text-xs"
                          onChange={(e) =>
                            patchMap(i, { tolerance: Number(e.target.value) || 0 })
                          }
                        />
                      </label>
                    )}
                    <label
                      className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      title="Spend an extra AI vision pass on this field when the text comparison disagrees."
                    >
                      <Checkbox
                        checked={m.verify_with_vision}
                        onCheckedChange={(v) =>
                          patchMap(i, { verify_with_vision: v === true })
                        }
                      />
                      Vision re-check
                    </label>
                    <label
                      className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      title="Flag the claim when it states this field but no document shows it."
                    >
                      <Checkbox
                        checked={m.require_evidence}
                        onCheckedChange={(v) =>
                          patchMap(i, { require_evidence: v === true })
                        }
                      />
                      Require evidence
                    </label>
                  </div>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={draft.field_maps.length >= MAX_FIELD_MAPS}
              onClick={() =>
                patch({
                  field_maps: [
                    ...draft.field_maps,
                    {
                      portal_field: "",
                      document_field: "",
                      mode: "fuzzy",
                      verify_with_vision: false,
                      require_evidence: false,
                    },
                  ],
                })
              }
            >
              <Plus className="size-3.5" />
              <span className="ml-1.5">Add field mapping</span>
            </Button>
          </EditorSection>

          <EditorSection
            title="Business rules"
            hint={
              <>
                Plain-language rules the AI judges against the claim and its
                documents. Only a failed CRITICAL rule can flag the claim;
                warning/info failures surface to you without flagging.
              </>
            }
          >
            <div className="space-y-2">
              {draft.ai_rules.map((r, i) => (
                <div
                  key={i}
                  className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5"
                >
                  <div className="flex items-start gap-2">
                    <textarea
                      value={r.rule}
                      rows={3}
                      maxLength={2000}
                      aria-label="Rule"
                      placeholder="e.g. The outstanding balance on the final bill must be $0."
                      className="flex-1 resize-y rounded-md border border-input bg-card p-2 text-xs leading-relaxed text-foreground shadow-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                      onChange={(e) => patchRule(i, { rule: e.target.value })}
                    />
                    <button
                      type="button"
                      className="grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-card hover:text-error"
                      aria-label={`Remove ${r.category || "this"} rule`}
                      onClick={() =>
                        patch({ ai_rules: draft.ai_rules.filter((_, j) => j !== i) })
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pl-0.5">
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      Category
                      <Input
                        value={r.category}
                        placeholder="general"
                        maxLength={64}
                        className="h-7 w-36 bg-card text-xs"
                        onChange={(e) => patchRule(i, { category: e.target.value })}
                      />
                    </label>
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      Severity
                      <NativeSelect
                        value={r.severity}
                        className="h-7 bg-card text-xs"
                        onChange={(e) =>
                          patchRule(i, { severity: e.target.value as ReviewSeverity })
                        }
                      >
                        <option value="critical">Critical — can flag the claim</option>
                        <option value="warning">Warning — surfaced only</option>
                        <option value="info">Info</option>
                      </NativeSelect>
                    </label>
                  </div>
                </div>
              ))}
            </div>
            {draft.ai_rules.length === 0 && (
              // Empty rules disable the built-in fraud checks for this claim type.
              <p className="rounded-md border border-warn/40 bg-warn-soft p-2.5 text-xs text-warn">
                No business rules — this claim type's reviews will only compare
                fields. The built-in checks (proof of treatment, patient
                identity, third-party billing, dates, treatment setting,
                diagnosis) will NOT run.
              </p>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={draft.ai_rules.length >= MAX_AI_RULES}
              onClick={() =>
                patch({
                  ai_rules: [
                    ...draft.ai_rules,
                    { rule: "", category: "general", severity: "critical" },
                  ],
                })
              }
            >
              <Plus className="size-3.5" />
              <span className="ml-1.5">Add rule</span>
            </Button>
          </EditorSection>

          <EditorSection
            title="Submission documents"
            hint="Required uploads and recognition rules are configured independently for each claim choice under Doc settings. The AI review checks that same snapshotted list."
          >
            <a
              href="/claims/review?tab=settings"
              className="inline-flex min-h-8 items-center text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Manage submission documents in Doc settings
            </a>
          </EditorSection>

          <ConfigurationHistory
            entityType="claim_review_config"
            entityId={target.configId}
            onRestore={(snapshot) => {
              if (
                !Array.isArray(snapshot.field_maps) ||
                !snapshot.field_maps.every(isReviewFieldMap) ||
                !Array.isArray(snapshot.ai_rules) ||
                !snapshot.ai_rules.every(isReviewAIRule)
              ) {
                toast.error("This saved version cannot be restored.");
                return;
              }
              patch({
                enabled:
                  typeof snapshot.enabled === "boolean"
                    ? snapshot.enabled
                    : draft.enabled,
                field_maps: snapshot.field_maps,
                ai_rules: snapshot.ai_rules,
              });
              toast.success(
                "Loaded the saved version. Review it, then save the setup.",
              );
            }}
          />

          <ReviewPromptPreview
            prompt={promptText}
            pending={preview.isPending}
            onPreview={showPreview}
          />
        </SheetBody>

        <SheetFooter>
          <Button type="button" variant="ghost" disabled={saving} onClick={requestClose}>
            Cancel
          </Button>
          <Button type="button" disabled={saving} onClick={save}>
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            <span className={saving ? "ml-1.5" : undefined}>Save rules</span>
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
    <AlertDialog
      open={confirmDiscard}
      onOpenChange={setConfirmDiscard}
      title="Discard unsaved rule changes?"
      description="Your edits to this claim type have not been saved."
      confirmLabel="Discard changes"
      confirmVariant="destructive"
      onConfirm={onClose}
    />
    </>
  );
}
