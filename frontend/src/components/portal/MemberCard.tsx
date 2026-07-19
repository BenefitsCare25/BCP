/** Panel e-card renderer — shared by the member portal, the broker "employee
 * view" preview and the config placement editor.
 *
 * The card is a pure join: the artwork is an <img>, and every printed value is
 * an absolutely-positioned span whose geometry comes from `placements` and
 * whose text comes from `values`. Both are resolved server-side
 * (`services/panel_cards.py`), so this component never reaches for member data.
 *
 * Geometry is fractional, so one placement record renders at any size: the box
 * gets `container-type: size` and font sizes are expressed in `cqh` (a
 * percentage of the card's own height) rather than px.
 *
 * The artwork itself rides an Authorization header, so it can't be loaded with
 * a plain src — each surface injects its own `useArtwork` hook (broker blob vs
 * portal blob), the same data-hook-injection pattern as ClinicLocator.
 */
import { useState } from "react";
import { ImageOff } from "lucide-react";
import type { CardFace, MemberCard, PlacementField } from "@/api/panelCards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type ArtworkHook = (
  cardId: string | null,
  face: CardFace,
  enabled?: boolean,
) => string | null;

const DEFAULT_ASPECT = 1012 / 638; // ISO/IEC 7810 ID-1 (a physical card)

function fieldStyle(field: PlacementField): React.CSSProperties {
  const shiftX =
    field.align === "center" ? "-50%" : field.align === "right" ? "-100%" : "0";
  return {
    position: "absolute",
    left: `${field.x * 100}%`,
    top: `${field.y * 100}%`,
    // y anchors the vertical CENTRE of the text — what a dragging user expects.
    transform: `translate(${shiftX}, -50%)`,
    fontSize: `${field.size * 100}cqh`,
    fontWeight: field.weight,
    color: field.color,
    textAlign: field.align,
    lineHeight: 1.15,
    textTransform: field.uppercase ? "uppercase" : "none",
    maxWidth: field.max_width ? `${field.max_width * 100}%` : undefined,
    whiteSpace: field.max_width ? "normal" : "nowrap",
  };
}

export function CardCanvas({
  aspectRatio,
  artworkSrc,
  fields,
  values,
  className,
  children,
}: {
  aspectRatio: number | null;
  artworkSrc: string | null;
  fields: PlacementField[];
  values: Record<string, string>;
  className?: string;
  /** Editor overlays (drag handles, guides). */
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-xl border border-border bg-muted shadow-sm",
        className,
      )}
      style={{
        aspectRatio: String(aspectRatio || DEFAULT_ASPECT),
        containerType: "size",
      }}
    >
      {artworkSrc ? (
        <img
          src={artworkSrc}
          alt=""
          className="absolute inset-0 size-full object-cover"
          draggable={false}
        />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
          <ImageOff className="size-6" />
          <span className="text-xs">No artwork uploaded</span>
        </div>
      )}
      {fields.map((field, index) => {
        const text = values[field.key] ?? "";
        if (!text) return null;
        return (
          <span
            key={`${field.key}-${index}`}
            style={fieldStyle(field)}
            className="pointer-events-none select-none"
          >
            {text}
          </span>
        );
      })}
      {children}
    </div>
  );
}

/** One member card with a front/back flip and the details that don't fit on
 * the artwork (covered services, remarks, special conditions). */
export function MemberCardView({
  card,
  useArtwork,
  className,
}: {
  card: MemberCard;
  useArtwork: ArtworkHook;
  className?: string;
}) {
  const [face, setFace] = useState<CardFace>("front");
  const showingBack = face === "back" && card.has_back;
  // Both faces are fetched so flipping doesn't flash; the back only when it exists.
  const front = useArtwork(card.card_id, "front");
  const back = useArtwork(card.card_id, "back", card.has_back);

  const fields = card.placements.fields.filter(
    (f) => f.face === (showingBack ? "back" : "front"),
  );
  const remarkEntries = Object.entries(card.remarks);

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {card.product_name}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {card.card_name}
            {card.holder_type === "dependant" && card.holder_name
              ? ` · ${card.holder_name}`
              : ""}
          </p>
        </div>
        {card.has_back && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setFace(showingBack ? "front" : "back")}
          >
            {showingBack ? "Show front" : "Show back"}
          </Button>
        )}
      </div>

      <CardCanvas
        aspectRatio={card.aspect_ratio}
        artworkSrc={showingBack ? back : front}
        fields={fields}
        values={card.values}
      />

      {card.services.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {card.services.map((service) => (
            <Badge key={service.key} variant="outline">
              {service.label}
            </Badge>
          ))}
        </div>
      )}

      {(remarkEntries.length > 0 || card.special_conditions) && (
        <div className="space-y-1 rounded-lg border border-border bg-card p-3">
          {remarkEntries.map(([key, value]) => (
            <p key={key} className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">
                {REMARK_LABELS[key] ?? key}:
              </span>{" "}
              {value}
            </p>
          ))}
          {card.special_conditions && (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">
                Special conditions:
              </span>{" "}
              {card.special_conditions}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// Mirrors CARD_REMARK_LABELS in models/panel_card.py.
const REMARK_LABELS: Record<string, string> = {
  gp: "GP",
  ae: "A&E",
  restructured_sp: "Restructured SP",
  private_sp: "Private SP",
  general: "General",
};

/** The card list shared by /portal/card and the employee-view preview tab. */
export function MemberCardList({
  cards,
  useArtwork,
  emptyMessage = "No e-cards have been issued for your plan yet.",
}: {
  cards: MemberCard[];
  useArtwork: ArtworkHook;
  emptyMessage?: string;
}) {
  if (cards.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center">
        <ImageOff className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm text-muted-foreground">{emptyMessage}</p>
      </div>
    );
  }
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      {cards.map((card) => (
        <MemberCardView
          key={`${card.assignment_id}-${card.holder_id}`}
          card={card}
          useArtwork={useArtwork}
        />
      ))}
    </div>
  );
}
