import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Minus, Plus, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import { cn } from "@/lib/cn";

const MIN_ZOOM = 1;
const MAX_ZOOM = 5;
const ZOOM_STEP = 0.25;

type Point = { x: number; y: number };

interface ClaimDocumentLightboxProps {
  fileName: string;
  mime: string;
  onCloseAutoFocus?: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  url: string;
}

export function ClaimDocumentLightbox({
  fileName,
  mime,
  onCloseAutoFocus,
  onOpenChange,
  open,
  url,
}: ClaimDocumentLightboxProps) {
  const isImage = mime.startsWith("image/");
  const viewportRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<{ pointerId: number; point: Point } | null>(null);
  const zoomRef = useRef(MIN_ZOOM);
  const offsetRef = useRef<Point>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 });

  function setView(nextZoom: number, nextOffset: Point) {
    zoomRef.current = nextZoom;
    offsetRef.current = nextOffset;
    setZoom(nextZoom);
    setOffset(nextOffset);
  }

  function resetView() {
    setView(MIN_ZOOM, { x: 0, y: 0 });
  }

  useEffect(() => {
    resetView();
  }, [open, url]);

  function constrainOffset(next: Point, nextZoom: number): Point {
    const viewport = viewportRef.current;
    const image = imageRef.current;
    if (!viewport || !image || image.naturalWidth === 0 || image.naturalHeight === 0) {
      return { x: 0, y: 0 };
    }

    const fitScale = Math.min(
      viewport.clientWidth / image.naturalWidth,
      viewport.clientHeight / image.naturalHeight,
    );
    const renderedWidth = image.naturalWidth * fitScale;
    const renderedHeight = image.naturalHeight * fitScale;
    const maxX = Math.max(0, (renderedWidth * nextZoom - viewport.clientWidth) / 2);
    const maxY = Math.max(0, (renderedHeight * nextZoom - viewport.clientHeight) / 2);

    return {
      x: Math.max(-maxX, Math.min(maxX, next.x)),
      y: Math.max(-maxY, Math.min(maxY, next.y)),
    };
  }

  function zoomTo(nextValue: number, clientPoint?: Point) {
    const nextZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextValue));
    const previousZoom = zoomRef.current;
    const viewport = viewportRef.current;
    const previousOffset = offsetRef.current;

    if (nextZoom === previousZoom || !viewport) return;

    const bounds = viewport.getBoundingClientRect();
    const anchor = clientPoint
      ? {
          x: clientPoint.x - bounds.left - bounds.width / 2,
          y: clientPoint.y - bounds.top - bounds.height / 2,
        }
      : { x: 0, y: 0 };
    const ratio = nextZoom / previousZoom;
    const nextOffset = constrainOffset(
      {
        x: anchor.x - (anchor.x - previousOffset.x) * ratio,
        y: anchor.y - (anchor.y - previousOffset.y) * ratio,
      },
      nextZoom,
    );

    setView(nextZoom, nextOffset);
  }

  useEffect(() => {
    if (!open || !isImage) return;

    const handleWheel = (event: WheelEvent) => {
      const viewport = viewportRef.current;
      if (!viewport || !(event.target instanceof Node) || !viewport.contains(event.target)) {
        return;
      }
      event.preventDefault();
      const multiplier = event.deltaY < 0 ? 1.15 : 1 / 1.15;
      zoomTo(zoomRef.current * multiplier, { x: event.clientX, y: event.clientY });
    };

    window.addEventListener("wheel", handleWheel, { passive: false });
    return () => window.removeEventListener("wheel", handleWheel);
  }, [isImage, open, url]);

  function panBy(delta: Point) {
    const nextOffset = constrainOffset(
      {
        x: offsetRef.current.x + delta.x,
        y: offsetRef.current.y + delta.y,
      },
      zoomRef.current,
    );
    setView(zoomRef.current, nextOffset);
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (zoomRef.current <= MIN_ZOOM || (event.pointerType === "mouse" && event.button !== 0)) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      point: { x: event.clientX, y: event.clientY },
    };
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    panBy({ x: event.clientX - drag.point.x, y: event.clientY - drag.point.y });
    dragRef.current = {
      pointerId: event.pointerId,
      point: { x: event.clientX, y: event.clientY },
    };
  }

  function stopDragging(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const panDistance = 48;
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      zoomTo(zoomRef.current + ZOOM_STEP);
    } else if (event.key === "-") {
      event.preventDefault();
      zoomTo(zoomRef.current - ZOOM_STEP);
    } else if (event.key === "0") {
      event.preventDefault();
      resetView();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      panBy({ x: panDistance, y: 0 });
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      panBy({ x: -panDistance, y: 0 });
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      panBy({ x: 0, y: panDistance });
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      panBy({ x: 0, y: -panDistance });
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[80] bg-black/90" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-[81] flex flex-col overflow-hidden bg-neutral-950 text-white focus:outline-none"
          aria-describedby="claim-document-lightbox-description"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            onCloseAutoFocus?.();
          }}
        >
          <DialogPrimitive.Title className="sr-only">
            Full-screen preview of {fileName}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description
            id="claim-document-lightbox-description"
            className="sr-only"
          >
            Review the selected claim document in a full-screen viewer.
          </DialogPrimitive.Description>

          <div className="flex h-16 shrink-0 items-center gap-3 border-b border-white/10 px-4 sm:px-5">
            <p className="min-w-0 flex-1 truncate text-sm font-medium text-white/80" title={fileName}>
              {fileName}
            </p>
            {isImage && (
              <div className="flex items-center rounded-md border border-white/15 bg-white/5" aria-label="Zoom controls">
                <button
                  type="button"
                  className="inline-flex size-10 items-center justify-center rounded-l-md text-white/80 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 disabled:cursor-not-allowed disabled:opacity-35"
                  aria-label="Zoom out"
                  disabled={zoom <= MIN_ZOOM}
                  onClick={() => zoomTo(zoomRef.current - ZOOM_STEP)}
                >
                  <Minus className="size-4" aria-hidden />
                </button>
                <output className="w-14 text-center text-xs tabular-nums text-white/80" aria-live="polite">
                  {Math.round(zoom * 100)}%
                </output>
                <button
                  type="button"
                  className="inline-flex size-10 items-center justify-center rounded-r-md text-white/80 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 disabled:cursor-not-allowed disabled:opacity-35"
                  aria-label="Zoom in"
                  disabled={zoom >= MAX_ZOOM}
                  onClick={() => zoomTo(zoomRef.current + ZOOM_STEP)}
                >
                  <Plus className="size-4" aria-hidden />
                </button>
              </div>
            )}
            {isImage && (
              <button
                type="button"
                className="inline-flex size-10 items-center justify-center rounded-md text-white/80 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
                aria-label="Reset document view"
                onClick={resetView}
              >
                <RotateCcw className="size-4" aria-hidden />
              </button>
            )}
            <DialogPrimitive.Close
              className="inline-flex size-10 items-center justify-center rounded-full bg-white/90 text-neutral-900 transition-colors hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-neutral-950"
              aria-label="Close full-screen preview"
            >
              <X className="size-5" aria-hidden />
            </DialogPrimitive.Close>
          </div>

          {isImage ? (
            <div
              ref={viewportRef}
              role="region"
              aria-label={`Interactive preview of ${fileName}`}
              aria-describedby="claim-document-gesture-hint"
              tabIndex={0}
              data-zoom={zoom.toFixed(2)}
              className={cn(
                "relative min-h-0 flex-1 touch-none overflow-hidden bg-neutral-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/70",
                zoom > MIN_ZOOM ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in",
              )}
              onDoubleClick={resetView}
              onKeyDown={handleKeyDown}
              onPointerCancel={stopDragging}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={stopDragging}
            >
              <img
                ref={imageRef}
                src={url}
                alt={`Preview of ${fileName}`}
                draggable={false}
                className="pointer-events-none h-full w-full select-none object-contain will-change-transform"
                style={{
                  transform: `translate3d(${offset.x}px, ${offset.y}px, 0) scale(${zoom})`,
                }}
              />
              <p
                id="claim-document-gesture-hint"
                className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-black/55 px-3 py-1.5 text-center text-xs text-white/70 backdrop-blur-sm"
              >
                Scroll to zoom · Drag to pan · Double-click to reset
              </p>
            </div>
          ) : (
            <iframe
              src={`${url}#view=Fit&toolbar=1&navpanes=0`}
              title={`Full-screen preview of ${fileName}`}
              className="min-h-0 flex-1 border-0 bg-white"
            />
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
