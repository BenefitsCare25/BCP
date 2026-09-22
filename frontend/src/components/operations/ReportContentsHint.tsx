import { useEffect, useRef, useState, type PointerEvent } from "react";
import { ListTree } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

export interface ReportContentSheet {
  title: string;
  description: string;
  columns?: string[];
}

export function ReportContentsHint({
  label,
  format,
  description,
  sheets = [],
}: {
  label: string;
  format: string;
  description?: string;
  sheets?: ReportContentSheet[];
}) {
  const [open, setOpen] = useState(false);
  const hoverOpen = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sheetLabel =
    sheets.length === 0
      ? null
      : `${sheets.length} ${sheets.length === 1 ? "sheet" : "sheets"}`;

  const cancelClose = () => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const openOnHover = (event: PointerEvent<HTMLElement>) => {
    if (event.pointerType === "touch") return;
    cancelClose();
    hoverOpen.current = true;
    setOpen(true);
  };

  const closeAfterHover = (event: PointerEvent<HTMLElement>) => {
    if (event.pointerType === "touch" || !hoverOpen.current) return;
    cancelClose();
    closeTimer.current = setTimeout(() => {
      hoverOpen.current = false;
      setOpen(false);
    }, 120);
  };

  useEffect(() => () => cancelClose(), []);

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        cancelClose();
        hoverOpen.current = false;
        setOpen(nextOpen);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`View ${label} contents`}
          onPointerEnter={openOnHover}
          onPointerLeave={closeAfterHover}
          className="focus-ring -my-1 inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-subtle transition-colors hover:bg-muted hover:text-foreground"
        >
          <ListTree className="size-3.5" aria-hidden="true" />
          <span>Contents</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        data-report-contents={label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}
        aria-label={`${label} contents`}
        tabIndex={0}
        side="bottom"
        align="start"
        collisionPadding={16}
        sticky="always"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onPointerEnter={cancelClose}
        onPointerLeave={closeAfterHover}
        style={{
          maxHeight:
            "min(calc(100vh - 3rem), var(--radix-popover-content-available-height))",
        }}
        className="box-border w-[min(32rem,calc(100vw-2rem))] max-w-none overflow-y-auto overscroll-contain p-0 text-muted-foreground"
      >
        <div className="p-4">
          <div className="flex items-baseline justify-between gap-4">
            <strong className="text-sm font-semibold text-foreground">
              {label}
            </strong>
            <span className="shrink-0 text-2xs uppercase tracking-wide text-subtle">
              {format}
              {sheetLabel ? ` · ${sheetLabel}` : ""}
            </span>
          </div>
          {description ? (
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
          {sheets.length > 0 ? (
            <div className="mt-3 divide-y divide-border border-t border-border">
              {sheets.map((sheet) => (
                <div key={sheet.title} className="py-2.5 first:pt-3">
                  <strong className="block text-xs font-semibold text-foreground">
                    {sheet.title}
                  </strong>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {sheet.description}
                  </p>
                  {sheet.columns?.length ? (
                    <p className="mt-1 text-xs leading-relaxed text-subtle">
                      <span className="font-medium text-muted-foreground">
                        Key columns:
                      </span>{" "}
                      {sheet.columns.join(" · ")}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
