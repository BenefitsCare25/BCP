/** Panel e-card CANVAS — the artwork plus its printed fields, shared by the
 * member portal (`leaf/CardLeaf.tsx`), the broker "employee view" preview and
 * the config placement editor.
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
 *
 * Everything member-FACING (which card, whose, what it entitles you to) lives
 * in `leaf/CardLeaf.tsx`. This file stays world-neutral because the placement
 * editor renders it inside the broker app.
 */
import { ImageOff, Loader2 } from "lucide-react";
import type { ArtworkState, CardFace, PlacementField } from "@/api/panelCards";
import { cn } from "@/lib/cn";

export type ArtworkHook = (
  cardId: string | null,
  face: CardFace,
  enabled?: boolean,
) => ArtworkState;

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
  artworkStatus = artworkSrc ? "ready" : "absent",
  fields,
  values,
  className,
  fallback = "No artwork uploaded",
  errorFallback = "The card design couldn't be loaded just now.",
  children,
}: {
  aspectRatio: number | null;
  artworkSrc: string | null;
  /** Defaulted from `artworkSrc` so the placement editor, which hands this a
   * plain URL, keeps its existing two-state behaviour untouched. */
  artworkStatus?: ArtworkState["status"];
  fields: PlacementField[];
  values: Record<string, string>;
  className?: string;
  /** What stands in for missing artwork. The broker is being told a file is
   * missing; a member needs to know their details still work at a clinic —
   * same absence, two different facts, so the caller supplies the words. */
  fallback?: string;
  /** Shown when the fetch FAILED, which is not the same fact as "no artwork
   * exists" and must not be reported as one — the design is there, this
   * screen just doesn't have it yet, and retrying may fix it. */
  errorFallback?: string;
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
      ) : artworkStatus === "loading" ? (
        // A spinner, not the "no artwork" message: every card shows this state
        // for the length of a blob fetch, and telling the member their insurer
        // supplied no design while it is still downloading is a wrong answer
        // that happens to be temporary.
        <div
          className="absolute inset-0 flex items-center justify-center text-muted-foreground"
          role="status"
          aria-label="Loading the card design"
        >
          <Loader2 className="size-6 animate-spin" aria-hidden />
        </div>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground">
          <ImageOff className="size-6" aria-hidden />
          <span className="text-xs leading-5">
            {artworkStatus === "error" ? errorFallback : fallback}
          </span>
        </div>
      )}
      {/* Placed fields need the artwork they were placed against. Without it
          they render at coordinates that mean nothing and land on top of the
          stand-in message — and on the member surface the same values are
          already printed legibly beneath the card. The placement editor draws
          its own handles and passes `fields={[]}`, so it is unaffected. */}
      {artworkSrc &&
        fields.map((field, index) => {
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
