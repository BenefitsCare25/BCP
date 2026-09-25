import { useEffect, useRef, useState, type ReactNode } from "react";

export type SkyPeriod = "day" | "dusk";

/** Morning and afternoon share the sunrise plate; from 6pm the valley turns to
 *  dusk — the same clock the greeting reads, so words and sky always agree. */
export function skyPeriod(date = new Date()): SkyPeriod {
  return date.getHours() >= 18 || date.getHours() < 5 ? "dusk" : "day";
}

/** The opening scene: a painted valley with the greeting in its open sky.
 *
 *  Motion is layered and slow — two cloud bands drifting at different speeds,
 *  the sun's glow breathing, the plate easing in — plus a small pointer
 *  parallax (fine pointers only). All of it stops under reduced motion; the
 *  still frame is complete on its own. The handwritten notes carry the
 *  member's own facts, never marketing lines. */
export function SkyStage({
  period,
  noteLeft,
  noteRight,
  children,
}: {
  period: SkyPeriod;
  noteLeft?: string | null;
  noteRight?: string | null;
  children: ReactNode;
}) {
  const stage = useRef<HTMLElement | null>(null);
  const [still] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  useEffect(() => {
    const node = stage.current;
    if (!node) return;
    if (!window.matchMedia("(pointer: fine)").matches) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = node.getBoundingClientRect();
        node.style.setProperty("--px", ((event.clientX - box.left) / box.width - 0.5).toFixed(3));
        node.style.setProperty("--py", ((event.clientY - box.top) / box.height - 0.5).toFixed(3));
      });
    };
    const onLeave = () => {
      node.style.setProperty("--px", "0");
      node.style.setProperty("--py", "0");
    };
    node.addEventListener("pointermove", onMove);
    node.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return (
    <section ref={stage} className="sky-stage" data-period={period} aria-label="Welcome">
      <div className="sky-plate" aria-hidden>
        {/* The river flows in a cinemagraph made from this same painting —
            only the water moves. Day only (the dusk plate has no loop yet);
            never under reduced motion, where the still plate is the scene. */}
        {period === "day" && !still && (
          <video
            className="sky-video"
            src="/portal/sky/day-flow.mp4"
            poster="/portal/sky/day.webp"
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
          />
        )}
      </div>
      <div className="sky-sun" aria-hidden />
      <div className="sky-clouds sky-clouds-far" aria-hidden />
      <div className="sky-clouds sky-clouds-near" aria-hidden />
      <div className="sky-content">{children}</div>
      {noteLeft && (
        <p className="sky-note sky-note-left" aria-hidden>
          {noteLeft}
          <svg viewBox="0 0 120 12" preserveAspectRatio="none"><path d="M2 8 C 30 2, 70 2, 118 7" /></svg>
        </p>
      )}
      {noteRight && (
        <p className="sky-note sky-note-right" aria-hidden>
          {noteRight}
          <svg viewBox="0 0 120 12" preserveAspectRatio="none"><path d="M2 6 C 40 10, 80 9, 118 4" /></svg>
        </p>
      )}
    </section>
  );
}
