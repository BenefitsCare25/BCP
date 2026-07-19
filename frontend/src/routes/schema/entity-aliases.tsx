import { useState } from "react";
import { ArrowRight, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateEntityAlias,
  useDeleteEntityAlias,
  useEntityAliases,
  useUpdateEntityAlias,
  type EntityAlias,
} from "@/api/entityAliases";
import { AlertDialog } from "@/components/ui/alert-dialog";
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
  canonical: string;
}

const EMPTY: Draft = { alias: "", canonical: "" };

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
  const [deleting, setDeleting] = useState<EntityAlias | null>(null);

  const onSheetOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setEditing(null);
      setDraft(EMPTY);
    }
  };

  const submit = async () => {
    const payload = {
      alias: draft.alias.trim(),
      canonical: draft.canonical.trim(),
    };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...payload });
        toast.success(`Updated ${payload.alias}`);
      } else {
        await create.mutateAsync(payload);
        toast.success(`Added ${payload.alias}`);
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <div className="space-y-4 max-w-7xl">
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
              hint="The spelling it should compare equal to — normally the registered name the placement slip carries."
            >
              <Input
                value={draft.canonical}
                onChange={(e) =>
                  setDraft({ ...draft, canonical: e.target.value })
                }
                placeholder="City Serviced Offices Pte Ltd"
              />
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
                !draft.canonical.trim() ||
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
                      {a.canonical}
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
                              canonical: a.canonical,
                            });
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
