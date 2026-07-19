/** Panel e-cards — a SHARED library of card artwork, assigned per benefit year.
 *
 * Two layers, mirroring panel clinic locations: a library card holds the
 * artwork + field placements (uploaded once per insurer/TPA, reused by every
 * company), and an assignment binds it to this company's benefit year and one
 * insurance product, carrying the data the card prints. Assigning is what makes
 * the card visible to members (`GET /portal/cards`).
 */
import { useRef, useState } from "react";
import {
  CreditCard,
  Image as ImageIcon,
  LayoutTemplate,
  Loader2,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import {
  useCreatePanelCard,
  useDeletePanelCard,
  useDeletePolicyYearCard,
  usePanelCards,
  usePolicyYearCards,
  useUpdatePanelCard,
  useUploadCardArtwork,
  type CardFace,
  type PanelCard,
  type PanelCardInput,
  type PolicyYearCard,
} from "@/api/panelCards";
import { CardAssignmentSheet } from "@/components/configuration/cards/CardAssignmentSheet";
import { PlacementEditor } from "@/components/configuration/cards/PlacementEditor";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InsurerSelect } from "@/components/configuration/InsurerSelect";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

const EMPTY_FORM: PanelCardInput = {
  insurer: "",
  panel_provider: "",
  name: "",
};

/** Mounted only while open (keyed by the edited card), so initial state is
 * always freshly seeded — no render-phase reseeding. */
function CardFormSheet({
  editing,
  onOpenChange,
}: {
  editing: PanelCard | null;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreatePanelCard();
  const update = useUpdatePanelCard();
  const [form, setForm] = useState<PanelCardInput>(() =>
    editing
      ? {
          insurer: editing.insurer,
          panel_provider: editing.panel_provider,
          name: editing.name,
        }
      : EMPTY_FORM,
  );
  const pending = create.isPending || update.isPending;
  const valid =
    form.insurer.trim() !== "" &&
    form.panel_provider.trim() !== "" &&
    form.name.trim() !== "";

  const submit = async () => {
    const payload = {
      insurer: form.insurer.trim(),
      panel_provider: form.panel_provider.trim(),
      name: form.name.trim(),
    };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...payload });
        toast.success("Card updated");
      } else {
        await create.mutateAsync(payload);
        toast.success("Card created — upload its artwork to make it usable");
      }
      onOpenChange(false);
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  return (
    <Sheet open onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{editing ? "Edit card" : "New card"}</SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="card-insurer">Insurer</Label>
            <InsurerSelect
              id="card-insurer"
              value={form.insurer}
              placeholder="e.g. AIA"
              onChange={(v) => setForm({ ...form, insurer: v })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="card-provider">Panel provider (TPA)</Label>
            <Input
              id="card-provider"
              value={form.panel_provider}
              placeholder="e.g. Parkway Shenton"
              onChange={(e) =>
                setForm({ ...form, panel_provider: e.target.value })
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="card-name">Card name</Label>
            <Input
              id="card-name"
              value={form.name}
              placeholder="e.g. AIA Parkway Shenton"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!valid || pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {editing ? "Save changes" : "Create card"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function ArtworkButton({
  card,
  face,
}: {
  card: PanelCard;
  face: CardFace;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useUploadCardArtwork();
  const uploaded = face === "front" ? card.has_front : card.has_back;

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      await upload.mutateAsync({ id: card.id, face, file });
      toast.success(`${face === "front" ? "Front" : "Back"} artwork uploaded`);
    } catch (error) {
      toast.error(formatError(error));
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".png,.jpg,.jpeg,.webp"
        className="hidden"
        onChange={(e) => void handleFile(e.target.files?.[0])}
      />
      <Button
        variant="ghost"
        size="sm"
        disabled={upload.isPending}
        onClick={() => fileRef.current?.click()}
        title={`Upload ${face} artwork${uploaded ? " (replaces the current image)" : ""}`}
      >
        {upload.isPending ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Upload className="size-4" />
        )}
        <span className="ml-1 text-xs">{face === "front" ? "Front" : "Back"}</span>
      </Button>
    </>
  );
}

function LibrarySection({
  cards,
  isLoading,
  onEdit,
  onLayout,
  onDelete,
}: {
  cards: PanelCard[];
  isLoading: boolean;
  onEdit: (card: PanelCard) => void;
  onLayout: (card: PanelCard) => void;
  onDelete: (card: PanelCard) => void;
}) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading cards…
      </div>
    );
  }
  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center">
        <CreditCard className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium text-foreground">
          The card library is empty
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Create a card per insurer and panel provider, upload its artwork, drag
          the printed fields into place, then assign it to a benefit year.
        </p>
      </div>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Insurer</TableHead>
          <TableHead>Panel provider</TableHead>
          <TableHead>Card</TableHead>
          <TableHead>Artwork</TableHead>
          <TableHead>Fields</TableHead>
          <TableHead title="Benefit years (across all companies) this card is assigned to">
            Used by
          </TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {cards.map((card) => (
          <TableRow key={card.id}>
            <TableCell className="font-medium text-foreground">
              {card.insurer}
            </TableCell>
            <TableCell>{card.panel_provider}</TableCell>
            <TableCell>{card.name}</TableCell>
            <TableCell>
              <div className="flex gap-1">
                {card.has_front ? (
                  <Badge variant="outline">Front</Badge>
                ) : (
                  <span className="text-xs text-warn">No front artwork</span>
                )}
                {card.has_back && <Badge variant="outline">Back</Badge>}
              </div>
            </TableCell>
            <TableCell className="tabular-nums">
              {card.placements.fields.length}
            </TableCell>
            <TableCell className="tabular-nums text-muted-foreground">
              {card.assigned_policy_year_ids.length}
            </TableCell>
            <TableCell>
              <div className="flex items-center justify-end gap-1">
                <ArtworkButton card={card} face="front" />
                <ArtworkButton card={card} face="back" />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!card.has_front}
                  onClick={() => onLayout(card)}
                  title="Position the printed fields on the artwork"
                >
                  <LayoutTemplate className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onEdit(card)}
                  title="Edit card"
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onDelete(card)}
                  title="Delete card"
                >
                  <Trash2 className="size-4 text-error" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function PanelCardsPanel({
  policyYearId,
  yearLabel,
}: {
  policyYearId: string | undefined;
  yearLabel: string;
}) {
  const { data: cards = [], isLoading } = usePanelCards();
  const { data: assignments = [] } = usePolicyYearCards(policyYearId);
  const removeCard = useDeletePanelCard();
  const removeAssignment = useDeletePolicyYearCard();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PanelCard | null>(null);
  const [layoutFor, setLayoutFor] = useState<PanelCard | null>(null);
  const [deleting, setDeleting] = useState<PanelCard | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [editingAssignment, setEditingAssignment] =
    useState<PolicyYearCard | null>(null);

  // The editor re-reads the card from the list so a fresh artwork upload (new
  // aspect ratio) is reflected without reopening it.
  const layoutCard = layoutFor
    ? (cards.find((c) => c.id === layoutFor.id) ?? layoutFor)
    : null;

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      await removeCard.mutateAsync(deleting.id);
      toast.success(`${deleting.display_label} deleted`);
      setDeleting(null);
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            Card library
            <InfoHint>
              Artwork is uploaded once per insurer and panel provider, then
              shared across companies. Assign it below to issue it for this
              benefit year.
            </InfoHint>
          </span>
          <Button
            size="sm"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus className="size-4" />
            <span className="ml-1">New card</span>
          </Button>
        </CardHeader>
        <CardContent>
          <LibrarySection
            cards={cards}
            isLoading={isLoading}
            onEdit={(card) => {
              setEditing(card);
              setFormOpen(true);
            }}
            onLayout={setLayoutFor}
            onDelete={setDeleting}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            Cards issued{yearLabel ? ` (${yearLabel})` : ""}
            <InfoHint>
              One card per insurance product. Members covered under that product
              see it in their portal; the assignment carries over when a new
              benefit year is created.
            </InfoHint>
          </span>
          <Button
            size="sm"
            disabled={!policyYearId}
            onClick={() => {
              setEditingAssignment(null);
              setAssignOpen(true);
            }}
          >
            <Plus className="size-4" />
            <span className="ml-1">Assign card</span>
          </Button>
        </CardHeader>
        <CardContent>
          {assignments.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-center">
              <ImageIcon className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-2 text-xs text-muted-foreground">
                No cards issued for this benefit year yet.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Card</TableHead>
                  <TableHead>Member ID</TableHead>
                  <TableHead>Services</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assignments.map((assignment) => (
                  <TableRow key={assignment.id}>
                    <TableCell className="font-medium text-foreground">
                      {assignment.product_name}
                    </TableCell>
                    <TableCell>{assignment.card_name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {assignment.employee_member_id_source.replace(/_/g, " ")}
                    </TableCell>
                    <TableCell className="tabular-nums text-muted-foreground">
                      {
                        Object.values(assignment.services).filter(Boolean)
                          .length
                      }
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setEditingAssignment(assignment);
                            setAssignOpen(true);
                          }}
                          title="Edit assignment"
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={removeAssignment.isPending}
                          onClick={() =>
                            removeAssignment.mutate(
                              {
                                policyYearId: policyYearId!,
                                assignmentId: assignment.id,
                              },
                              {
                                onSuccess: () =>
                                  toast.success(
                                    `${assignment.product_name} card withdrawn — members no longer see it`,
                                  ),
                              },
                            )
                          }
                          title="Withdraw this card"
                        >
                          <Trash2 className="size-4 text-error" />
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

      {formOpen && (
        <CardFormSheet
          key={editing?.id ?? "new"}
          editing={editing}
          onOpenChange={setFormOpen}
        />
      )}

      {assignOpen && policyYearId && (
        <CardAssignmentSheet
          key={editingAssignment?.id ?? "new"}
          policyYearId={policyYearId}
          cards={cards}
          editing={editingAssignment}
          onOpenChange={setAssignOpen}
        />
      )}

      {layoutCard && (
        <Sheet open onOpenChange={(open) => !open && setLayoutFor(null)}>
          <SheetContent className="sm:max-w-4xl">
            <SheetHeader>
              <SheetTitle>Card layout — {layoutCard.display_label}</SheetTitle>
            </SheetHeader>
            <SheetBody>
              <PlacementEditor
                key={layoutCard.id}
                card={layoutCard}
                onClose={() => setLayoutFor(null)}
              />
            </SheetBody>
          </SheetContent>
        </Sheet>
      )}

      <AlertDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete card?"
        description={
          deleting
            ? `${deleting.display_label}, its artwork and its layout will be removed from the shared library and withdrawn from every benefit year it is issued for (${deleting.assigned_policy_year_ids.length}). Members will no longer see this card.`
            : ""
        }
        loading={removeCard.isPending}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
