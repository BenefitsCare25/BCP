import { useState } from "react";
import { Globe, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCreateInsurer,
  useDeleteInsurer,
  useInsurers,
  useUpdateInsurer,
  type Insurer,
  type InsurerInput,
} from "@/api/insurers";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Field, InfoHint } from "@/components/ui/tooltip";
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
  name: string;
  legal_name: string;
  aliases: string;
  notes: string;
}

const EMPTY_DRAFT: Draft = { name: "", legal_name: "", aliases: "", notes: "" };

function toDraft(i: Insurer): Draft {
  return {
    name: i.name,
    legal_name: i.legal_name ?? "",
    aliases: i.aliases.join(", "),
    notes: i.notes ?? "",
  };
}

function toPayload(draft: Draft): InsurerInput {
  return {
    name: draft.name.trim(),
    legal_name: draft.legal_name.trim() || null,
    aliases: draft.aliases
      .split(",")
      .map((a) => a.trim())
      .filter(Boolean),
    notes: draft.notes.trim() || null,
  };
}

export function SchemaInsurersPage({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: insurers = [], isLoading } = useInsurers();
  const create = useCreateInsurer();
  const update = useUpdateInsurer();
  const remove = useDeleteInsurer();
  const [editing, setEditing] = useState<Insurer | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [deleting, setDeleting] = useState<Insurer | null>(null);

  const onSheetOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setEditing(null);
      setDraft(EMPTY_DRAFT);
    }
  };

  const beginEdit = (i: Insurer) => {
    setEditing(i);
    setDraft(toDraft(i));
    onOpenChange(true);
  };

  const submit = async () => {
    const payload = toPayload(draft);
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...payload });
        toast.success(`Updated ${payload.name}`);
      } else {
        await create.mutateAsync(payload);
        toast.success(`Added ${payload.name}`);
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
            <div className="flex items-center gap-1.5">
              <SheetTitle>{editing ? "Edit insurer" : "Add insurer"}</SheetTitle>
              <InfoHint>
                {editing
                  ? editing.client_id === null
                    ? "Editing a library entry — changes apply to all clients."
                    : "Updates this insurer for the current client only."
                  : "Adds an insurer scoped to the current client."}
              </InfoHint>
            </div>
          </SheetHeader>
          <SheetBody className="space-y-4">
            <Field
              label="Name"
              hint="The short name brokers type and that gets stored on the product (e.g. AIA, MSIG). Reports group by this exact string, so keep it stable — renaming it here does not rewrite products that already carry the old spelling."
            >
              <Input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="AIA"
              />
            </Field>
            <Field
              label="Full legal name (optional)"
              hint="The MAS-licensed entity name. Reference only — never used to match or group."
            >
              <Input
                value={draft.legal_name}
                onChange={(e) =>
                  setDraft({ ...draft, legal_name: e.target.value })
                }
                placeholder="AIA Singapore Private Limited"
              />
            </Field>
            <Field
              label="Aliases (optional)"
              hint="Comma-separated other spellings seen on placement slips and rosters, including former names. Used to stop the same insurer being added twice under different names."
            >
              <Input
                value={draft.aliases}
                onChange={(e) => setDraft({ ...draft, aliases: e.target.value })}
                placeholder="AIA Singapore, AIA Group"
              />
            </Field>
            <Field label="Notes (optional)">
              <Input
                value={draft.notes}
                onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
                placeholder="Formerly AXA Insurance Pte Ltd"
              />
            </Field>
          </SheetBody>
          <SheetFooter>
            <SheetClose asChild>
              <Button variant="outline">Cancel</Button>
            </SheetClose>
            <Button
              onClick={submit}
              disabled={
                !draft.name.trim() || create.isPending || update.isPending
              }
            >
              {editing ? "Save changes" : "Save insurer"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <SkeletonTable rows={6} columns={5} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Full legal name</TableHead>
                  <TableHead>Aliases</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead className="w-[100px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {insurers.map((i) => (
                  <TableRow key={i.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-1.5">
                        {i.name}
                        {i.in_use && <Badge variant="outline">In use</Badge>}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {i.legal_name ?? "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {i.aliases.length ? i.aliases.join(", ") : "—"}
                    </TableCell>
                    <TableCell>
                      {i.client_id === null ? (
                        <Badge variant="default" className="gap-1">
                          <Globe className="size-3" /> Library
                        </Badge>
                      ) : (
                        <Badge variant="default">Client-specific</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1 justify-end">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => beginEdit(i)}
                          aria-label="Edit insurer"
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => setDeleting(i)}
                          aria-label="Delete insurer"
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
        purpose="The list of insurers available when setting up a product. Each entry records the short name brokers use, the full licensed entity name, and any other spellings seen on placement slips. Seeded with the Singapore market; add your own for anything missing."
        connections={[
          {
            label: "→ Products catalog",
            description:
              "The Insurer field on a product is a dropdown of these names",
          },
          {
            label: "→ Reports",
            description:
              "Insurer reports group products by the insurer name stored on them",
          },
          {
            label: "→ Rosters",
            description:
              "'<Insurer> Member ID' roster columns are matched by this same name",
          },
        ]}
      />

      <AlertDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Remove ${deleting?.name ?? ""} from the list?`}
        description={
          deleting?.in_use ? (
            <>
              <code>{deleting.name}</code> is currently set on at least one
              product. Those products keep the name and keep reporting under it
              — removing this entry only takes it out of the dropdown, so it
              can't be picked again without re-adding it.
            </>
          ) : (
            <>
              <code>{deleting?.name}</code> will no longer be offered when
              choosing an insurer for a product. No existing data changes.
            </>
          )
        }
        confirmLabel="Remove insurer"
        loading={remove.isPending}
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await remove.mutateAsync(deleting.id);
            toast.success(`Removed ${deleting.name}`);
            setDeleting(null);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </div>
  );
}
