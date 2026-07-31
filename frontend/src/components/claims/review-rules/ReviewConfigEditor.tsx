/** Editor for one claim type's AI review rule setup.
 *
 * Opens as a sheet with a local draft: field mappings (claim ↔ document pairs
 * with match mode + vision re-check), AI business rules (severity-graded —
 * only a CRITICAL failure can flag a claim), and the required-documents
 * override (empty keeps the automatic derivation from the claim type).
 * Nothing persists until Save; the prompt preview renders the DRAFT.
 */
import { useEffect, useState } from "react";
import { Eye, Loader2, Plus, Trash2, X } from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

const selectClass =
  "h-7 rounded-md border border-border bg-background px-2 text-xs text-foreground focus-ring disabled:opacity-50";

const SEVERITY_BADGE: Record<ReviewSeverity, "error" | "warn" | "info"> = {
  critical: "error",
  warning: "warn",
  info: "info",
};

export interface EditorTarget {
  /** null = creating the claim type's first custom setup. */
  configId: string | null;
  draft: ClaimReviewConfigInput;
}

export function ReviewConfigEditor({
  target,
  onClose,
}: {
  target: EditorTarget | null;
  onClose: () => void;
}) {
  const create = useCreateClaimReviewConfig();
  const update = useUpdateClaimReviewConfig();
  const preview = usePreviewReviewPrompt();
  const [draft, setDraft] = useState<ClaimReviewConfigInput | null>(null);
  const [promptText, setPromptText] = useState<string | null>(null);
  const [reqDocDraft, setReqDocDraft] = useState("");

  useEffect(() => {
    setDraft(target ? target.draft : null);
    setPromptText(null);
    setReqDocDraft("");
  }, [target]);

  if (!target || !draft) return null;
  const saving = create.isPending || update.isPending;

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
    const maps = draft.field_maps.filter(
      (m) => m.portal_field.trim() && m.document_field.trim(),
    );
    if (maps.length === 0) {
      toast.error("Add at least one field mapping.");
      return;
    }
    const body: ClaimReviewConfigInput = {
      ...draft,
      field_maps: maps,
      ai_rules: draft.ai_rules.filter((r) => r.rule.trim()),
      required_documents: draft.required_documents.filter((d) => d.trim()),
    };
    const done = { onSuccess: onClose, onError: (e: unknown) => toast.error(formatError(e)) };
    if (target.configId) update.mutate({ id: target.configId, ...body }, done);
    else create.mutate(body, done);
  };

  const showPreview = () => {
    preview.mutate(draft, {
      onSuccess: (r) => setPromptText(r.prompt),
      onError: (e) => toast.error(formatError(e)),
    });
  };

  const addReqDoc = () => {
    const v = reqDocDraft.trim();
    if (!v) return;
    if (!draft.required_documents.some((d) => d.toLowerCase() === v.toLowerCase())) {
      patch({ required_documents: [...draft.required_documents, v] });
    }
    setReqDocDraft("");
  };

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{draft.display_label} — review rules</SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-6">
          <label className="flex items-center gap-2 text-sm text-foreground">
            <Switch
              checked={draft.enabled}
              onCheckedChange={(v) => patch({ enabled: v })}
            />
            Use this setup
            <InfoHint>
              Switched off, this claim type's reviews fall back to the built-in
              default rules without losing your configuration.
            </InfoHint>
          </label>

          {/* Field mappings */}
          <section className="space-y-2">
            <div className="flex items-center gap-1">
              <h3 className="text-sm font-semibold text-foreground">Field mappings</h3>
              <InfoHint>
                Which claim-form field is checked against which document field,
                and how strictly. <strong>Vision re-check</strong> spends an
                extra AI image pass when the text comparison disagrees (a cost
                control). <strong>Require evidence</strong> flags the claim when
                it states a value that no document substantiates — keep it on
                for money, dates and the provider even if you switch vision off.
              </InfoHint>
            </div>
            <div className="space-y-2">
              {draft.field_maps.map((m, i) => (
                <div
                  key={i}
                  className="flex flex-wrap items-center gap-1.5 rounded-md border border-border p-2"
                >
                  <Input
                    value={m.portal_field}
                    placeholder="Claim field (e.g. amount_claimed)"
                    className="h-7 w-44 text-xs"
                    onChange={(e) => patchMap(i, { portal_field: e.target.value })}
                  />
                  <span className="text-xs text-muted-foreground">↔</span>
                  <Input
                    value={m.document_field}
                    placeholder="Document field (e.g. Total Amount)"
                    className="h-7 w-52 text-xs"
                    onChange={(e) => patchMap(i, { document_field: e.target.value })}
                  />
                  <select
                    className={selectClass}
                    value={m.mode}
                    onChange={(e) =>
                      patchMap(i, {
                        mode: e.target.value as ReviewMatchMode,
                        tolerance: e.target.value === "numeric" ? (m.tolerance ?? 0.01) : null,
                      })
                    }
                  >
                    <option value="fuzzy">Fuzzy</option>
                    <option value="exact">Exact</option>
                    <option value="numeric">Numeric</option>
                  </select>
                  {m.mode === "numeric" && (
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={m.tolerance ?? 0}
                      className="h-7 w-20 text-xs"
                      title="Tolerance"
                      onChange={(e) =>
                        patchMap(i, { tolerance: Number(e.target.value) || 0 })
                      }
                    />
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
                  <button
                    type="button"
                    className="ml-auto text-muted-foreground hover:text-error"
                    aria-label="Remove field mapping"
                    onClick={() =>
                      patch({ field_maps: draft.field_maps.filter((_, j) => j !== i) })
                    }
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
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
          </section>

          {/* AI business rules */}
          <section className="space-y-2">
            <div className="flex items-center gap-1">
              <h3 className="text-sm font-semibold text-foreground">Business rules</h3>
              <InfoHint>
                Plain-language rules the AI judges against the claim and its
                documents. Only a failed CRITICAL rule can flag the claim;
                warning/info failures surface to you without flagging.
              </InfoHint>
            </div>
            <div className="space-y-2">
              {draft.ai_rules.map((r, i) => (
                <div key={i} className="rounded-md border border-border p-2 space-y-1.5">
                  <div className="flex items-start gap-1.5">
                    <textarea
                      value={r.rule}
                      rows={2}
                      placeholder="e.g. The outstanding balance on the final bill must be $0."
                      className="flex-1 rounded-md border border-border bg-background p-2 text-xs text-foreground focus-ring"
                      onChange={(e) => patchRule(i, { rule: e.target.value })}
                    />
                    <button
                      type="button"
                      className="mt-1 text-muted-foreground hover:text-error"
                      aria-label="Remove rule"
                      onClick={() =>
                        patch({ ai_rules: draft.ai_rules.filter((_, j) => j !== i) })
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={r.category}
                      placeholder="Category"
                      className="h-7 w-40 text-xs"
                      onChange={(e) => patchRule(i, { category: e.target.value })}
                    />
                    <select
                      className={selectClass}
                      value={r.severity}
                      onChange={(e) =>
                        patchRule(i, { severity: e.target.value as ReviewSeverity })
                      }
                    >
                      <option value="critical">Critical — can flag the claim</option>
                      <option value="warning">Warning — surfaced only</option>
                      <option value="info">Info</option>
                    </select>
                    <Badge variant={SEVERITY_BADGE[r.severity]}>{r.severity}</Badge>
                  </div>
                </div>
              ))}
            </div>
            {draft.ai_rules.length === 0 && (
              // A setup with no rules is legitimate (field comparisons still
              // run) but it removes the built-in fraud checks for this claim
              // type — never let that pass unnoticed.
              <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs text-foreground">
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
          </section>

          {/* Required documents override */}
          <section className="space-y-1.5">
            <div className="flex items-center gap-1">
              <h3 className="text-sm font-semibold text-foreground">
                Additional required documents
              </h3>
              <InfoHint>
                Extra document families to require for this claim type. The
                automatic list always applies on top — it varies by sub-type,
                hospital sector and referral rules, which a per-claim-type list
                cannot express, so these ADD to it rather than replace it.
              </InfoHint>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {draft.required_documents.map((d, i) => (
                <span
                  key={`${d}-${i}`}
                  className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs text-foreground"
                >
                  {d}
                  <button
                    type="button"
                    aria-label={`Remove ${d}`}
                    className="text-muted-foreground hover:text-foreground"
                    onClick={() =>
                      patch({
                        required_documents: draft.required_documents.filter(
                          (_, j) => j !== i,
                        ),
                      })
                    }
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
              <Input
                value={reqDocDraft}
                placeholder="e.g. receipt or tax invoice"
                className="h-7 w-56 text-xs"
                onChange={(e) => setReqDocDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addReqDoc();
                  }
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                disabled={!reqDocDraft.trim()}
                onClick={addReqDoc}
              >
                <Plus className="size-3.5" />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground/70">
              {draft.required_documents.length === 0
                ? "None — the automatic list for this claim type applies."
                : "Required in addition to the automatic list for this claim type."}
            </p>
          </section>

          {/* Prompt preview */}
          <section className="space-y-1.5">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={preview.isPending}
              onClick={showPreview}
            >
              {preview.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Eye className="size-3.5" />
              )}
              <span className="ml-1.5">Preview AI prompt</span>
            </Button>
            {promptText && (
              <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted p-3 text-[11px] leading-relaxed text-foreground whitespace-pre-wrap">
                {promptText}
              </pre>
            )}
          </section>

          <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="button" disabled={saving} onClick={save}>
              {saving && <Loader2 className="size-3.5 animate-spin" />}
              <span className={saving ? "ml-1.5" : undefined}>Save rules</span>
            </Button>
          </div>
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
