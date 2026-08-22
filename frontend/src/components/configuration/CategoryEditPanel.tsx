import { useState } from "react";
import { Loader2, Save, Sparkles, Trash2 } from "lucide-react";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { InfoHint } from "@/components/ui/tooltip";
import { RuleBuilder } from "@/components/primitives/RuleBuilder";
import {
  useAIStatus,
  useAISuggest,
  useConfirmCategory,
  useDeleteCategory,
  usePatchCategory,
} from "@/api/hooks";
import type { AttributeSchema, Category, RuleNode } from "@/types";
import { confidencePill, sourcePill, statusPill } from "@/lib/badges";
import { formatError } from "@/lib/errors";
import { toast } from "sonner";

interface Props {
  category: Category | null;
  schema: AttributeSchema[];
  onClose: () => void;
}

/**
 * A category "covers everyone" when its rule is an empty AND group
 * (`{"and": []}`) — the matching engine treats that as always-true with
 * specificity 0, so any more-specific category in the same product still wins.
 */
function isCoversAllRule(rule: RuleNode): boolean {
  if (!rule || typeof rule !== "object") return false;
  const keys = Object.keys(rule);
  if (keys.length !== 1 || keys[0] !== "and") return false;
  const branches = (rule as Record<string, unknown>).and;
  return Array.isArray(branches) && branches.length === 0;
}

export function CategoryEditPanel({ category, schema, onClose }: Props) {
  return (
    <Sheet open={!!category} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="sm:max-w-2xl">
        {category && (
          <EditForm
            key={category.id}
            category={category}
            schema={schema}
            onClose={onClose}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function EditForm({
  category,
  schema,
  onClose,
}: {
  category: Category;
  schema: AttributeSchema[];
  onClose: () => void;
}) {
  const [displayName, setDisplayName] = useState(category.display_name);
  const [rule, setRule] = useState<RuleNode>(category.matching_rule);
  const [current, setCurrent] = useState(category);
  const patch = usePatchCategory();
  const confirm = useConfirmCategory();
  const aiSuggest = useAISuggest();
  const deleteCategory = useDeleteCategory();
  const { data: aiStatus } = useAIStatus();
  const [showDelete, setShowDelete] = useState(false);
  const validation = current.rule_validation ?? {};
  const validationErrors = Array.isArray(validation.errors)
    ? validation.errors.map(String)
    : [];
  const validationWarnings = Array.isArray(validation.warnings)
    ? validation.warnings.map(String)
    : [];
  const matchedCount =
    typeof validation.matched_count === "number" ? validation.matched_count : null;
  const expectedCount =
    typeof validation.expected_count === "number" ? validation.expected_count : null;

  const coversAll = isCoversAllRule(rule);
  const toggleCoversAll = (on: boolean) => setRule(on ? { and: [] } : null);

  const ruleChanged = JSON.stringify(rule) !== JSON.stringify(current.matching_rule);
  const nameChanged = displayName.trim() !== current.display_name;
  const pendingPatch = () => ({
    ...(nameChanged ? { display_name: displayName.trim() } : {}),
    ...(ruleChanged
      ? {
          matching_rule: rule,
          // Never retain an AI reading after the broker changes its rule.
          rule_human_readable: coversAll ? "All employees" : null,
        }
      : {}),
  });

  const save = async () => {
    try {
      if (nameChanged || ruleChanged) {
        await patch.mutateAsync({
          id: category.id,
          patch: pendingPatch(),
        });
        toast.success("Category updated");
      }
      onClose();
    } catch (reason) {
      toast.error(formatError(reason));
    }
  };

  const onConfirm = async () => {
    try {
      // Confirm the rule visible in this panel, not a stale server snapshot.
      // This also makes manual edits pass the backend's company validator first.
      if (nameChanged || ruleChanged) {
        await patch.mutateAsync({
          id: category.id,
          patch: pendingPatch(),
        });
      }
      await confirm.mutateAsync(category.id);
      toast.success("Category confirmed and saved for this company");
      onClose();
    } catch (reason) {
      toast.error(formatError(reason));
    }
  };

  const onAISuggest = async () => {
    try {
      const updated = await aiSuggest.mutateAsync(category.id);
      setRule(updated.matching_rule);
      setCurrent(updated);
      const matched = updated.rule_validation?.matched_count;
      toast.success(
        `AI suggested and checked a rule (${Math.round((updated.confidence ?? 0) * 100)}% confidence)`,
        {
          description:
            typeof matched === "number"
              ? `${matched} active employee match${matched === 1 ? "" : "es"}; broker confirmation is still required.`
              : "Saved for broker review; confirmation is still required.",
        },
      );
    } catch (e) {
      const message = formatError(e);
      if (message.startsWith("No safe suggestion available")) {
        toast.info("No safe suggestion available", {
          description: message.replace(/^No safe suggestion available:\s*/, ""),
        });
      } else {
        toast.error(message);
      }
    }
  };

  return (
    <>
      <SheetHeader>
        <div className="flex items-center gap-2">
          {sourcePill(current.source)}
          {statusPill(current.status)}
          {confidencePill(current.confidence)}
          {current.rule_status && (
            <Badge
              variant={
                current.rule_status === "validated"
                  ? "good"
                  : current.rule_status === "unmapped"
                    ? "error"
                    : "warn"
              }
            >
              {current.rule_status.replaceAll("_", " ")}
            </Badge>
          )}
        </div>
        <SheetTitle>{category.display_name}</SheetTitle>
        {category.source_ref && (
          <p className="text-2xs font-mono text-muted-foreground truncate">
            {category.source_ref}
          </p>
        )}
      </SheetHeader>
      <SheetBody className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label>Display name</Label>
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Raw description from slip</Label>
          <div className="text-sm rounded-md border border-border bg-muted/40 p-3 whitespace-pre-wrap">
            {category.raw_description}
          </div>
        </div>

        {current.rule_human_readable && (
          <div className="flex flex-col gap-1.5">
            <Label>Rule interpretation</Label>
            <div className="space-y-1 rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="break-words">{current.rule_human_readable}</p>
              {(matchedCount !== null || expectedCount !== null) && (
                <p className="text-xs text-muted-foreground">
                  {matchedCount !== null
                    ? `${matchedCount} active employee match${matchedCount === 1 ? "" : "es"}`
                    : "Employee match not available"}
                  {expectedCount !== null
                    ? ` · ${expectedCount} employee${expectedCount === 1 ? "" : "s"} stated on the slip`
                    : ""}
                </p>
              )}
            </div>
          </div>
        )}

        <Separator />

        {(validationErrors.length > 0 || validationWarnings.length > 0) && (
          <div className="rounded-md border border-warn/40 bg-warn-soft/40 p-3 text-xs">
            <p className="font-medium text-foreground">Mapping checks</p>
            <ul className="mt-1 space-y-1 text-warn">
              {[...validationErrors, ...validationWarnings].map((message) => (
                <li key={message}>• {message}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1">
              <Label>Employee matching rule</Label>
              <InfoHint>
                AI and automatic proposals may only use non-PII employee fields
                and values that exist for this company. Review before confirming.
              </InfoHint>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={onAISuggest}
              disabled={!aiStatus?.configured || aiSuggest.isPending || coversAll}
              title={
                aiStatus?.configured
                  ? "Ask Gemini to map this wording against the company's non-PII employee values and sibling plans"
                  : "AI not configured — set up an AI provider in settings"
              }
            >
              {aiSuggest.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Sparkles className="size-3.5" />
              )}
              {aiSuggest.isPending
                ? "Checking employee listing…"
                : aiStatus?.configured
                  ? "Suggest rule with AI"
                  : "AI not configured"}
            </Button>
          </div>

          <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/40 px-3 py-2.5">
            <div className="flex items-center gap-1">
              <Label htmlFor="covers-all" className="cursor-pointer">
                Covers all employees
              </Label>
              <InfoHint>
                Every employee (all job grades) matches this category. New hires
                are covered automatically on each matching run.
              </InfoHint>
            </div>
            <Switch
              id="covers-all"
              checked={coversAll}
              onCheckedChange={toggleCoversAll}
            />
          </div>

          {coversAll ? (
            <p className="px-1 text-xs italic text-muted-foreground">
              Matches everyone in the policy year — the condition builder is
              disabled. Turn this off to match on specific attributes.
            </p>
          ) : (
            <RuleBuilder rule={rule} schema={schema} onChange={setRule} />
          )}
        </div>

        <details className="rounded-md border border-border bg-muted/40">
          <summary className="cursor-pointer select-none px-3 py-2.5 text-xs font-medium">
            Technical rule JSON
          </summary>
          <pre className="overflow-x-auto border-t border-border px-3 py-2.5 text-2xs font-mono whitespace-pre-wrap text-muted-foreground">
            {JSON.stringify(rule, null, 2)}
          </pre>
        </details>

        {current.human_modified && (
          <div className="flex items-center gap-1">
            <Badge variant="outline">Human-modified</Badge>
            <InfoHint>Source flipped from AI to manual on last edit.</InfoHint>
          </div>
        )}
      </SheetBody>
      <SheetFooter>
        <Button
          variant="ghost"
          onClick={() => setShowDelete(true)}
          className="text-error hover:text-error mr-auto"
        >
          <Trash2 className="size-4" /> Delete
        </Button>
        <SheetClose asChild>
          <Button variant="outline">Cancel</Button>
        </SheetClose>
        {current.status !== "confirmed" && (
          <Button
            variant="secondary"
            onClick={onConfirm}
            disabled={confirm.isPending || patch.isPending || rule === null}
          >
            {confirm.isPending && <Loader2 className="size-4 animate-spin" />}
            Confirm mapping
          </Button>
        )}
        <Button
          onClick={save}
          disabled={patch.isPending || (!nameChanged && !ruleChanged)}
        >
          {patch.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save changes
        </Button>
      </SheetFooter>

      <AlertDialog
        open={showDelete}
        onOpenChange={setShowDelete}
        title="Delete this category?"
        description={
          <>
            Permanently removes{" "}
            <strong>{category.display_name}</strong>. The deletion is logged
            in the audit trail. This cannot be undone.
          </>
        }
        loading={deleteCategory.isPending}
        onConfirm={async () => {
          try {
            await deleteCategory.mutateAsync(category.id);
            toast.success("Category deleted");
            setShowDelete(false);
            onClose();
          } catch (reason) {
            toast.error(formatError(reason));
          }
        }}
      />
    </>
  );
}
