/** Drag-to-position editor for a card's printed fields.
 *
 * Placements are fractions of the artwork box, so dragging just converts a
 * pointer position into (x, y) fractions of the canvas rect — the same numbers
 * the member portal renders with, at any size. Preview text is sample data:
 * the real values are resolved per member server-side.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  CARD_FACES,
  DEFAULT_PLACEMENT,
  useCardOptions,
  useSetCardPlacements,
  type CardFace,
  type PanelCard,
  type PlacementField,
} from "@/api/panelCards";
import { useBrokerCardArtwork } from "@/api/panelCards";
import { CardCanvas } from "@/components/portal/MemberCard";
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
import { cn } from "@/lib/cn";

/** Plausible stand-ins so the broker can judge spacing before any member exists. */
const SAMPLE_VALUES: Record<string, string> = {
  member_name: "ALICE TAN MEI LING",
  member_id: "2427617201",
  staff_id: "E10432",
  email: "alice.tan@company.com",
  nric_masked: "*****567D",
  company_name: "Acme Holdings Pte Ltd",
  policy_number: "G-99887766",
  product_name: "Group Clinical GP",
  plan_name: "Plan A",
  effective_date: "2026-01-01",
  expiry_date: "2026-12-31",
  insurer: "AIA Singapore",
  panel_provider: "Parkway Shenton",
  card_name: "AIA Parkway Shenton",
  special_conditions: "Co-pay $5 per visit",
  dependant_name: "BOB TAN",
  relationship: "Spouse",
  remark_gp: "Present this card at any panel GP.",
  remark_ae: "A&E visits require pre-authorisation.",
  remark_restructured_sp: "Referral letter required.",
  remark_private_sp: "Referral letter required.",
  remark_general: "Not transferable.",
};

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function PlacementEditor({
  card,
  onClose,
}: {
  card: PanelCard;
  onClose: () => void;
}) {
  const { data: options } = useCardOptions();
  const save = useSetCardPlacements();
  const [fields, setFields] = useState<PlacementField[]>(
    () => card.placements.fields,
  );
  const [face, setFace] = useState<CardFace>("front");
  const [selected, setSelected] = useState<number | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const front = useBrokerCardArtwork(card.id, "front");
  const back = useBrokerCardArtwork(card.id, "back", card.has_back);

  const values = useMemo(() => {
    const out: Record<string, string> = {};
    for (const field of fields) out[field.key] = SAMPLE_VALUES[field.key] ?? field.key;
    return out;
  }, [fields]);

  const visible = fields
    .map((field, index) => ({ field, index }))
    .filter(({ field }) => field.face === face);

  const updateField = (index: number, patch: Partial<PlacementField>) => {
    setFields((prev) =>
      prev.map((field, i) => (i === index ? { ...field, ...patch } : field)),
    );
  };

  const startDrag = useCallback(
    (index: number, event: React.PointerEvent<HTMLElement>) => {
      event.preventDefault();
      setSelected(index);
      const canvas = canvasRef.current;
      if (!canvas) return;
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);

      const move = (e: PointerEvent) => {
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        setFields((prev) =>
          prev.map((field, i) =>
            i === index
              ? {
                  ...field,
                  x: clamp01((e.clientX - rect.left) / rect.width),
                  y: clamp01((e.clientY - rect.top) / rect.height),
                }
              : field,
          ),
        );
      };
      const up = () => {
        target.releasePointerCapture(event.pointerId);
        target.removeEventListener("pointermove", move);
        target.removeEventListener("pointerup", up);
      };
      target.addEventListener("pointermove", move);
      target.addEventListener("pointerup", up);
    },
    [],
  );

  const addField = (key: string) => {
    setFields((prev) => [...prev, { ...DEFAULT_PLACEMENT, face, key }]);
    setSelected(fields.length);
  };

  const removeField = (index: number) => {
    setFields((prev) => prev.filter((_, i) => i !== index));
    setSelected(null);
  };

  const submit = async () => {
    try {
      await save.mutateAsync({ id: card.id, fields });
      toast.success("Card layout saved");
      onClose();
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  const used = new Set(fields.map((f) => f.key));
  const active = selected !== null ? fields[selected] : undefined;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="space-y-3">
        <div className="flex items-center gap-1">
          {CARD_FACES.map((value) => (
            <Button
              key={value}
              size="sm"
              variant={face === value ? "default" : "ghost"}
              disabled={value === "back" && !card.has_back}
              onClick={() => {
                setFace(value);
                setSelected(null);
              }}
            >
              {value === "front" ? "Front" : "Back"}
            </Button>
          ))}
          <span className="ml-2 text-xs text-muted-foreground">
            Drag a field onto the artwork to position it.
          </span>
        </div>

        <CardCanvas
          aspectRatio={card.aspect_ratio}
          artworkSrc={(face === "back" ? back : front).url}
          artworkStatus={(face === "back" ? back : front).status}
          // Positioned text is drawn by the drag handles below, not the canvas.
          fields={[]}
          values={{}}
          className="select-none"
        >
          <div ref={canvasRef} className="absolute inset-0">
            {visible.map(({ field, index }) => (
              <button
                key={index}
                type="button"
                onPointerDown={(e) => startDrag(index, e)}
                onClick={() => setSelected(index)}
                className={cn(
                  "absolute cursor-grab touch-none whitespace-nowrap rounded px-0.5 outline-offset-2 active:cursor-grabbing",
                  selected === index
                    ? "outline outline-2 outline-primary"
                    : "outline-dashed outline-1 outline-muted-foreground/50",
                )}
                style={{
                  left: `${field.x * 100}%`,
                  top: `${field.y * 100}%`,
                  transform: `translate(${
                    field.align === "center"
                      ? "-50%"
                      : field.align === "right"
                        ? "-100%"
                        : "0"
                  }, -50%)`,
                  fontSize: `${field.size * 100}cqh`,
                  fontWeight: field.weight,
                  color: field.color,
                  textTransform: field.uppercase ? "uppercase" : "none",
                  lineHeight: 1.15,
                }}
              >
                {values[field.key]}
              </button>
            ))}
          </div>
        </CardCanvas>
      </div>

      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label>Add a field</Label>
          <Select value="" onValueChange={addField}>
            <SelectTrigger>
              <SelectValue placeholder="Choose a field…" />
            </SelectTrigger>
            <SelectContent>
              {(options?.placement_keys ?? [])
                .filter((option) => !used.has(option.key))
                .map((option) => (
                  <SelectItem key={option.key} value={option.key}>
                    {option.label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          {visible.length === 0 && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Plus className="size-3" /> No fields on this face yet.
            </p>
          )}
        </div>

        {active && selected !== null ? (
          <div className="space-y-3 rounded-lg border border-border p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-foreground">
                {options?.placement_keys.find((o) => o.key === active.key)
                  ?.label ?? active.key}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => removeField(selected)}
                title="Remove field"
              >
                <Trash2 className="size-4 text-error" />
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label className="text-xs">Size (% of height)</Label>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  step={0.5}
                  value={Math.round(active.size * 1000) / 10}
                  onChange={(e) =>
                    updateField(selected, {
                      size: Math.min(
                        0.5,
                        Math.max(0.005, Number(e.target.value) / 100),
                      ),
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Weight</Label>
                <Select
                  value={String(active.weight)}
                  onValueChange={(v) =>
                    updateField(selected, { weight: Number(v) })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[300, 400, 500, 600, 700, 800].map((w) => (
                      <SelectItem key={w} value={String(w)}>
                        {w}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Align</Label>
                <Select
                  value={active.align}
                  onValueChange={(v) =>
                    updateField(selected, {
                      align: v as PlacementField["align"],
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="left">Left</SelectItem>
                    <SelectItem value="center">Center</SelectItem>
                    <SelectItem value="right">Right</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Colour</Label>
                <Input
                  type="color"
                  value={active.color}
                  className="h-9 p-1"
                  onChange={(e) =>
                    updateField(selected, { color: e.target.value })
                  }
                />
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs text-foreground">
              <Checkbox
                checked={active.uppercase}
                onCheckedChange={(v) =>
                  updateField(selected, { uppercase: v === true })
                }
              />
              Uppercase
            </label>
            <label className="flex items-center gap-2 text-xs text-foreground">
              <Checkbox
                checked={active.max_width !== null}
                onCheckedChange={(v) =>
                  updateField(selected, { max_width: v === true ? 0.4 : null })
                }
              />
              Wrap long text
            </label>
            {active.max_width !== null && (
              <div className="space-y-1">
                <Label className="text-xs">Max width (% of card)</Label>
                <Input
                  type="number"
                  min={5}
                  max={100}
                  value={Math.round(active.max_width * 100)}
                  onChange={(e) =>
                    updateField(selected, {
                      max_width: Math.min(
                        1,
                        Math.max(0.05, Number(e.target.value) / 100),
                      ),
                    })
                  }
                />
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Select a field on the card to change its size, weight or alignment.
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void submit()} disabled={save.isPending}>
            {save.isPending && <Loader2 className="size-4 animate-spin" />}
            Save layout
          </Button>
        </div>
      </div>
    </div>
  );
}
