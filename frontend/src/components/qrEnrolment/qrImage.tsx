/** A QR code a broker can show, download as a captioned PNG and send.
 *
 * The PNG is what HR forwards or prints, so it carries its own context — who it
 * is for, which period, until when — and stays dark-on-white whatever theme the
 * screen uses: scanners and printers need that contrast. Ink and paper are read
 * from the theme tokens and only fall back to fixed values off-screen. */
import { useRef } from "react";
import { QRCodeCanvas, QRCodeSVG } from "qrcode.react";
import { cn } from "@/lib/cn";

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export interface QrCaption {
  title: string;
  lines: string[];
  footer?: string;
}

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const words = text.split(/\s+/);
  const out: string[] = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > width && line) {
      out.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line) out.push(line);
  return out;
}

export function composeQrPng(source: HTMLCanvasElement, caption: QrCaption): Promise<Blob> {
  const W = 1000;
  const pad = 72;
  const qr = 640;
  const font = token("--font-sans", "Inter, Arial, sans-serif");
  const ink = token("--color-foreground", "#1f2937");
  const muted = token("--color-muted-foreground", "#4b5563");
  const paper = "#ffffff";
  const probe = document.createElement("canvas").getContext("2d")!;
  probe.font = `600 44px ${font}`;
  const title = wrap(probe, caption.title, W - pad * 2);
  probe.font = `400 30px ${font}`;
  const lines = caption.lines.flatMap(l => wrap(probe, l, W - pad * 2));
  const footer = caption.footer ? wrap(probe, caption.footer, W - pad * 2) : [];
  const H = pad + title.length * 56 + 16 + lines.length * 42 + 40 + qr + 40 + footer.length * 38 + pad;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = paper;
  ctx.fillRect(0, 0, W, H);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  let y = pad;
  ctx.fillStyle = ink;
  ctx.font = `600 44px ${font}`;
  for (const t of title) { ctx.fillText(t, W / 2, y); y += 56; }
  y += 16;
  ctx.fillStyle = muted;
  ctx.font = `400 30px ${font}`;
  for (const l of lines) { ctx.fillText(l, W / 2, y); y += 42; }
  y += 40;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(source, (W - qr) / 2, y, qr, qr);
  y += qr + 40;
  ctx.fillStyle = muted;
  for (const f of footer) { ctx.fillText(f, W / 2, y); y += 38; }
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => (blob ? resolve(blob) : reject(new Error("Could not create the image."))), "image/png");
  });
}

/** The visible QR plus a hidden high-resolution twin for the PNG. */
export function QrPreview({
  value,
  size = 168,
  canvasRef,
  className,
  label,
}: {
  value: string;
  size?: number;
  canvasRef: React.RefObject<HTMLCanvasElement>;
  className?: string;
  label: string;
}) {
  return (
    <div className={cn("inline-flex rounded-lg border border-border bg-card p-3", className)}>
      <QRCodeSVG value={value} size={size} level="M" marginSize={2} title={label}
        bgColor={token("--color-card", "#ffffff")} fgColor={token("--color-foreground", "#111827")} />
      <QRCodeCanvas ref={canvasRef} value={value} size={1024} level="M" marginSize={4}
        bgColor="#ffffff" fgColor="#000000" className="hidden" aria-hidden />
    </div>
  );
}

export function useQrCanvas() {
  return useRef<HTMLCanvasElement>(null);
}
