/** Assign a card to this benefit year for one insurance product.
 *
 * The assignment carries everything that changes per renewal: which identifier
 * is printed as the member ID, the covered-service badges and the per-setting
 * remarks. The service flags are ENTITLEMENTS the broker asserts — they are
 * deliberately not derived from the tagged clinic networks (a plan can cover
 * X-ray & lab with no clinic list loaded, and a tagged dental network does not
 * mean the plan pays dental).
 */
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  useCardOptions,
  useCreatePolicyYearCard,
  useUpdatePolicyYearCard,
  type PanelCard,
  type PolicyYearCard,
  type PolicyYearCardInput,
} from "@/api/panelCards";
import { useCategoriesGrouped } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { InfoHint } from "@/components/ui/tooltip";

function emptyForm(cardId: string): PolicyYearCardInput {
  return {
    panel_card_id: cardId,
    product_id: "",
    employee_member_id_source: "insurer_member_id",
    dependant_member_id_source: "insurer_member_id",
    services: {},
    remarks: {},
    special_conditions: "",
    show_future_cards: false,
  };
}

export function CardAssignmentSheet({
  policyYearId,
  cards,
  editing,
  onOpenChange,
}: {
  policyYearId: string;
  cards: PanelCard[];
  editing: PolicyYearCard | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: options } = useCardOptions();
  // Only products this benefit year actually configures — a card assigned to a
  // product the year has no categories for would never resolve for any member
  // (build_member_cards skips products with no coverage line), so it would sit
  // in the "Cards issued" table looking healthy while issuing nothing.
  const { data: productGroups = [] } = useCategoriesGrouped(policyYearId);
  const products = productGroups.flatMap((group) =>
    group.product_id
      ? [
          {
            id: group.product_id,
            code: group.product_code,
            display_name: group.product_display_name,
          },
        ]
      : [],
  );
  const create = useCreatePolicyYearCard();
  const update = useUpdatePolicyYearCard();
  // Only cards with front artwork can be assigned (the API enforces this too).
  const assignable = cards.filter((c) => c.has_front);
  const [form, setForm] = useState<PolicyYearCardInput>(() =>
    editing
      ? {
          panel_card_id: editing.panel_card_id,
          product_id: editing.product_id,
          employee_member_id_source: editing.employee_member_id_source,
          dependant_member_id_source: editing.dependant_member_id_source,
          services: editing.services,
          remarks: editing.remarks,
          special_conditions: editing.special_conditions ?? "",
          show_future_cards: editing.show_future_cards,
        }
      : emptyForm(assignable[0]?.id ?? ""),
  );

  const pending = create.isPending || update.isPending;
  const valid = Boolean(form.panel_card_id && form.product_id);

  const submit = async () => {
    const body = {
      ...form,
      special_conditions: form.special_conditions?.trim() || null,
    };
    try {
      if (editing) {
        await update.mutateAsync({
          policyYearId,
          assignmentId: editing.id,
          body,
        });
        toast.success("Card assignment updated");
      } else {
        await create.mutateAsync({ policyYearId, body });
        toast.success("Card assigned — members can now see it in their portal");
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
          <SheetTitle>
            {editing ? "Edit card assignment" : "Assign a card"}
          </SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-4">
          <div className="space-y-1.5">
            <Label>Card</Label>
            <Select
              value={form.panel_card_id}
              onValueChange={(v) => setForm({ ...form, panel_card_id: v })}
            >
              <SelectTrigger>
                <SelectValue placeholder="Choose a card…" />
              </SelectTrigger>
              <SelectContent>
                {assignable.map((card) => (
                  <SelectItem key={card.id} value={card.id}>
                    {card.display_label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {assignable.length === 0 && (
              <p className="text-xs text-warn">
                Upload front artwork for a card before assigning it.
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="flex items-center gap-1.5">
              Insurance product
              <InfoHint>
                The card is issued to members covered under this product. A
                member with no coverage for it sees no card.
              </InfoHint>
            </Label>
            <Select
              value={form.product_id}
              onValueChange={(v) => setForm({ ...form, product_id: v })}
            >
              <SelectTrigger>
                <SelectValue placeholder="Choose a product…" />
              </SelectTrigger>
              <SelectContent>
                {products.map((product) => (
                  <SelectItem key={product.id} value={product.id}>
                    {product.display_name} ({product.code})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {products.length === 0 && (
              <p className="text-xs text-warn">
                This benefit year has no configured products yet — upload a
                placement slip first.
              </p>
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5">
                Employee member ID
                <InfoHint>
                  Which identifier is printed in the card's Member ID field.
                  "Platform-generated" issues a stable ID that survives
                  renewals, for panels that don't supply their own number.
                </InfoHint>
              </Label>
              <Select
                value={form.employee_member_id_source}
                onValueChange={(v) =>
                  setForm({ ...form, employee_member_id_source: v })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(options?.member_id_sources ?? []).map((option) => (
                    <SelectItem key={option.key} value={option.key}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Dependant member ID</Label>
              <Select
                value={form.dependant_member_id_source}
                onValueChange={(v) =>
                  setForm({ ...form, dependant_member_id_source: v })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(options?.member_id_sources ?? []).map((option) => (
                    <SelectItem key={option.key} value={option.key}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="flex items-center gap-1.5">
              Covered services
              <InfoHint>
                Printed on the card as entitlement badges. Set these from the
                plan's benefits — they are not inferred from which clinic
                networks are enabled.
              </InfoHint>
            </Label>
            <div className="grid grid-cols-2 gap-1">
              {(options?.services ?? []).map((service) => (
                <label
                  key={service.key}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-muted/60"
                >
                  <Checkbox
                    checked={form.services[service.key] === true}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        services: {
                          ...form.services,
                          [service.key]: v === true,
                        },
                      })
                    }
                  />
                  {service.label}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Default remarks</Label>
            {(options?.remark_keys ?? []).map((remark) => (
              <div key={remark.key} className="space-y-1">
                <Label className="text-xs text-muted-foreground">
                  {remark.label}
                </Label>
                <Input
                  value={form.remarks[remark.key] ?? ""}
                  placeholder={`Shown on the card for ${remark.label}`}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      remarks: { ...form.remarks, [remark.key]: e.target.value },
                    })
                  }
                />
              </div>
            ))}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="card-special">Special conditions</Label>
            <Input
              id="card-special"
              value={form.special_conditions ?? ""}
              placeholder="e.g. Co-pay $5 per visit"
              onChange={(e) =>
                setForm({ ...form, special_conditions: e.target.value })
              }
            />
          </div>

          <label className="flex items-start gap-2 text-sm text-foreground">
            <Checkbox
              checked={form.show_future_cards}
              onCheckedChange={(v) =>
                setForm({ ...form, show_future_cards: v === true })
              }
            />
            <span>
              Show future cards
              <span className="block text-xs text-muted-foreground">
                Saved now; members currently see the current benefit year's card
                only.
              </span>
            </span>
          </label>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!valid || pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {editing ? "Save changes" : "Assign card"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
