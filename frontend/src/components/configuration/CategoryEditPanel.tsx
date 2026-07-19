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
  const patch = usePatchCategory();
  const confirm = useConfirmCategory();
  const aiSuggest = useAISuggest();
  const deleteCategory = useDeleteCategory();
  const { data: aiStatus } = useAIStatus();
  const [showDelete, setShowDelete] = useState(false);

  const coversAll = isCoversAllRule(rule);
  const wasCoversAll = isCoversAllRule(category.matching_rule);
  const toggleCoversAll = (on: boolean) => setRule(on ? { and: [] } : null);

  const save = async () => {
    await patch.mutateAsync({
      id: category.id,
      patch: {
        display_name: displayName,
        matching_rule: rule,
        // Keep the generator's reading honest: label covers-all explicitly, and
        // clear a stale "All employees" label once a real rule replaces it.
        // Untouched otherwise so AI/manual readings survive unrelated edits.
        ...(coversAll
          ? { rule_human_readable: "All employees" }
          : wasCoversAll
            ? { rule_human_readable: null }
            : {}),
      },
    });
    toast.success("Category updated");
    onClose();
  };

  const onConfirm = async () => {
    await confirm.mutateAsync(category.id);
    toast.success("Category confirmed");
    onClose();
  };

  const onAISuggest = async () => {
    try {
      const updated = await aiSuggest.mutateAsync(category.id);
      setRule(updated.matching_rule);
      toast.success(
        `AI suggested a rule (${Math.round((updated.confidence ?? 0) * 100)}% confidence)`,
      );
    } catch (e) {
      toast.error(formatError(e));
    }
  };

  return (
    <>
      <SheetHeader>
        <div className="flex items-center gap-2">
          {sourcePill(category.source)}
          {statusPill(category.status)}
          {confidencePill(category.confidence)}
        </div>
        <SheetTitle>{category.display_name}</SheetTitle>
        {category.source_ref && (
          <p className="text-[10px] font-mono text-muted-foreground truncate">
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

        {category.rule_human_readable && (
          <div className="flex flex-col gap-1.5">
            <Label>Generator's reading</Label>
            <div className="text-sm rounded-md border border-border bg-muted/40 p-3 font-mono">
              {category.rule_human_readable}
            </div>
          </div>
        )}

        <Separator />

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <Label>Matching rule (JSONLogic)</Label>
            <Button
              size="sm"
              variant="outline"
              onClick={onAISuggest}
              disabled={!aiStatus?.configured || aiSuggest.isPending || coversAll}
              title={
                aiStatus?.configured
                  ? "Ask Claude (via Azure Foundry) to generate a rule from the raw description"
                  : "AI not configured — set AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_API_KEY"
              }
            >
              {aiSuggest.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Sparkles className="size-3.5" />
              )}
              {aiStatus?.configured ? "Suggest via AI" : "AI not configured"}
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

        <div className="rounded-md border border-border bg-muted/40 p-3">
          <Label>Live JSON</Label>
          <pre className="text-[11px] font-mono whitespace-pre-wrap mt-2 text-muted-foreground">
            {JSON.stringify(rule, null, 2)}
          </pre>
        </div>

        {category.human_modified && (
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
        {category.status !== "confirmed" && (
          <Button
            variant="secondary"
            onClick={onConfirm}
            disabled={confirm.isPending}
          >
            {confirm.isPending && <Loader2 className="size-4 animate-spin" />}
            Confirm as-is
          </Button>
        )}
        <Button onClick={save} disabled={patch.isPending}>
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
          await deleteCategory.mutateAsync(category.id);
          toast.success("Category deleted");
          setShowDelete(false);
          onClose();
        }}
      />
    </>
  );
}
