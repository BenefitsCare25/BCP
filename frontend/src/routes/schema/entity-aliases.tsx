import { useState } from "react";
import { ArrowRight, Pencil, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateEntityAlias,
  useDeleteEntityAlias,
  useEntityAliases,
  useUpdateEntityAlias,
  type EntityAlias,
} from "@/api/entityAliases";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/tooltip";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { SkeletonTable } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatError } from "@/lib/errors";
import { PageGuide } from "@/components/ui/page-guide";

interface Draft {
  alias: string;
  canonicals: string[];
}

const EMPTY: Draft = { alias: "", canonicals: [] };

const norm = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

export function SchemaEntityAliasesPage({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: aliases = [], isLoading } = useEntityAliases();
  const create = useCreateEntityAlias();
  const update = useUpdateEntityAlias();
  const remove = useDeleteEntityAlias();
  const [editing, setEditing] = useState<EntityAlias | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [entityInput, setEntityInput] = useState("");
  const [deleting, setDeleting] = useState<EntityAlias | null>(null);

  const onSheetOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setEditing(null);
      setDraft(EMPTY);
      setEntityInput("");
    }
  };

  const addEntity = () => {
    const value = entityInput.trim();
    if (!value) return;
    if (draft.canonicals.some((c) => norm(c) === norm(value))) {
      setEntityInput("");
      return;
    }
    setDraft((d) => ({ ...d, canonicals: [...d.canonicals, value] }));
    setEntityInput("");
  };

  const removeEntity = (index: number) =>
    setDraft((d) => ({
      ...d,
      canonicals: d.canonicals.filter((_, i) => i !== index),
    }));

  // Fold a half-typed entity in on submit so it isn't silently dropped.
  const pending = entityInput.trim();
  const finalCanonicals =
    pending && !draft.canonicals.some((c) => norm(c) === norm(pending))
      ? [...draft.canonicals, pending]
      : draft.canonicals;

  const submit = async () => {
    const payload = { alias: draft.alias.trim(), canonicals: finalCanonicals };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...payload });
        toast.success(`Updated ${payload.alias}`);
      } else {
        await create.mutateAsync(payload);
        toast.success(`Added ${payload.alias}`);
      }
      // Reset editing/draft too — a bare onOpenChange(false) would leave
      // `editing` set, so the next "Add alias" reopens pre-filled and its save
      // would overwrite the just-edited row.
      onSheetOpenChange(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4">
      <Sheet open={open} onOpenChange={onSheetOpenChange}>
        <SheetContent side="right">
          <SheetHeader>
            <SheetTitle>{editing ? "Edit alias" : "Add entity alias"}</SheetTitle>
          </SheetHeader>
          <SheetBody className="space-y-4">
            <Field
              label="Alias"
              hint="The spelling that appears on one side but not the other — usually the roster's abbreviation or a former name."
            >
              <Input
                value={draft.alias}
                onChange={(e) => setDraft({ ...draft, alias: e.target.value })}
                placeholder="CSO"
              />
            </Field>
            <Field
              label="Same entity as"
              hint="The registered name(s) the placement slip carries. Add more than one when a single roster spelling covers several subsidiaries."
            >
              <div className="space-y-2">
                {draft.canonicals.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {draft.canonicals.map((c, i) => (
                      <Badge key={`${c}-${i}`} variant="outline" className="gap-1 pr-1">
                        {c}
                        <button
                          type="button"
                          onClick={() => removeEntity(i)}
                          aria-label={`Remove ${c}`}
                          className="rounded-full p-0.5 text-muted-foreground hover:text-foreground"
                        >
                          <X className="size-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <Input
                    value={entityInput}
                    onChange={(e) => setEntityInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addEntity();
                      }
                    }}
                    placeholder="City Serviced Offices Pte Ltd"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={addEntity}
                    disabled={!entityInput.trim()}
                    aria-label="Add entity"
                  >
                    <Plus className="size-3.5" />
                    Add
                  </Button>
                </div>
              </div>
            </Field>
            <p className="text-xs text-muted-foreground">
              Neither name is rewritten. Categories keep the registered name (the
              exported placement slip reproduces it verbatim) and the roster keeps
              its own — the alias only changes how the two compare when matching.
            </p>
          </SheetBody>
          <SheetFooter>
            <SheetClose asChild>
              <Button variant="outline">Cancel</Button>
            </SheetClose>
            <Button
              onClick={submit}
              disabled={
                !draft.alias.trim() ||
                finalCanonicals.length === 0 ||
                create.isPending ||
                update.isPending
              }
            >
              {editing ? "Save changes" : "Save alias"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <SkeletonTable rows={4} columns={3} />
          ) : aliases.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No aliases yet. Add one when a category's insured entity and the
              roster's Entity column spell the same company differently.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Alias</TableHead>
                  <TableHead>Same entity as</TableHead>
                  <TableHead className="w-[100px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {aliases.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        {a.alias}
                        <ArrowRight className="size-3 text-muted-foreground" />
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {a.canonicals.length > 1 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {a.canonicals.map((c, i) => (
                            <Badge key={`${c}-${i}`} variant="outline">
                              {c}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        a.canonicals[0] ?? a.canonical
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1 justify-end">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => {
                            setEditing(a);
                            setDraft({
                              alias: a.alias,
                              canonicals:
                                a.canonicals.length > 0
                                  ? a.canonicals
                                  : [a.canonical],
                            });
                            setEntityInput("");
                            onOpenChange(true);
                          }}
                          aria-label="Edit alias"
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => setDeleting(a)}
                          aria-label="Delete alias"
                          className="text-error hover:text-error"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <PageGuide
        purpose="Bridges two spellings of the same legal entity so employee matching treats them as one. Needed when a category's Insured entity (the registered name, taken from the placement slip) differs from the roster's Entity column — an abbreviation, a former name, or a trading name."
        connections={[
          {
            label: "→ Employee matching",
            description:
              "A category naming entities only matches employees of those entities; aliases widen that comparison",
          },
          {
            label: "→ Product setup",
            description:
              "The Insured picker flags entities that match no roster value — those are the ones needing an alias",
          },
        ]}
      />

      <AlertDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Remove the ${deleting?.alias ?? ""} alias?`}
        description={
          <>
            <code>{deleting?.alias}</code> will no longer compare equal to{" "}
            <code>{deleting?.canonical}</code>. Employees matched only through
            this alias become unmatched on the next matching run. No entity name
            is changed.
          </>
        }
        confirmLabel="Remove alias"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await remove.mutateAsync(deleting.id);
            toast.success(`Removed ${deleting.alias}`);
            setDeleting(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </div>
  );
}
