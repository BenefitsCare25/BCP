/** The deck — an index and a stage, in place of a stack.
 *
 * `/portal/coverage` used to render one mount per product down a single column;
 * a fully covered member holds nine to eleven of them, each carrying its own
 * schedule, and the page ran to nine screenfuls. That serves neither thing a
 * member opens it to do: a targeted lookup ("is dental covered, and for how
 * much?") means scrolling past eight products they did not want, and there is
 * no view in which the set is visible at once.
 *
 * **The rail is the reason this is better than the stack, not the animation.**
 * A swipe-only carousel would make the lookup strictly worse — linear traversal
 * of a set you cannot see. Everything the member holds is named in the rail, the
 * rail is sticky, and the stage carries one product at a time.
 *
 * ── Four decisions that are easy to undo by accident ──────────────────────
 *
 * **ONE tablist element, relaid out — never two copies toggled with `hidden`.**
 * The rail is a horizontal pill below `wide` and a vertical list above it, and
 * the obvious implementation is to render both and hide one. That works for a
 * heading (`PortalShell` does exactly that with its two `h1`s) and it does NOT
 * work here: a tablist owns roving focus and `aria-controls` wiring, and a
 * second live copy duplicates both. `HeadRail` carries the same note for the
 * same reason.
 *
 * **The layout switches on the DECK'S OWN width, not the viewport's.** Two
 * surfaces mount this — the member's page and the broker's employee-view
 * preview, whose column is far narrower than the window it sits in. A viewport
 * media query would hand that column a two-column desktop layout it cannot fit.
 *
 * **The pill is measured and transformed, not a layout animation.** `layoutId`
 * is the idiomatic motion.dev answer and it is the wrong one here: the rail is
 * a horizontally scrolling container that this component ALSO scrolls
 * programmatically when the active chip is out of view, and a layout projection
 * measured across a concurrent scroll lands the pill somewhere it does not
 * belong. Measuring the active chip's offset within the track is
 * scroll-independent by construction.
 *
 * **Height never animates.** The outgoing slide is taken out of flow, so the
 * stage adopts the incoming slide's height in one frame. Springing the height
 * between a four-row schedule and a sixty-nine-row one is where a component like
 * this janks, and the scroll correction below puts that change off-screen anyway.
 *
 * **The transition is CSS keyframes and a timer, not a presence library.** This
 * was `AnimatePresence mode="popLayout"` and the swap is the same; what differed
 * was who owns removal. Under rapid switching — tapping through nine products,
 * which is exactly what a deck invites — exits stacked instead of replacing one
 * another and lingered for seconds, seven full schedules deep in the DOM. Here
 * `exiting` is one piece of state removed on a known deadline, so "at most one
 * outgoing slide, gone in 200ms" is a property of the code rather than a hope
 * about a scheduler. It is also how every other animation in this world is
 * written (`leaf-rise`, `leaf-grow`), and it costs one `useEffect`. */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useReducedMotion } from "motion/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { glassSurface, glassHover } from "./Mount";
import { useContainerWide } from "./useContainerWide";

export interface DeckSlide {
  /** Stable across renders; it is what the URL carries. */
  key: string;
  /** Member language, short. Never a product code — see `productShortLabel`. */
  label: string;
  /** A one- or two-word state for this slide, shown in the rail.
   *
   * **A deck hides everything it is not showing, which is fine for a page you
   * READ and not fine for a page you DECIDE on.** On the enrollment deck a
   * member can change their hospital plan, move on, and have no way to see that
   * they did without navigating back to it. The mark is what turns the index
   * into a record of the decisions made: "Changed", "Declined".
   *
   * Printed beside the label where the rail is a vertical list, and reduced to
   * a dot where it is a horizontal pill — a chip carrying "Hospital  Changed"
   * on a phone is either truncated or twice as wide, and the word survives in
   * the accessibility tree either way. Keep it SHORT; it is a mark, not a
   * sentence. */
  mark?: string;
  render: () => ReactNode;
}

/** The width at which the rail becomes a vertical list beside the stage. Below
 * it, a sticky horizontal pill. Measured on the deck, not the window. */
const WIDE_AT = 720;

/** Past this the drag is a swipe rather than a scroll that wandered. */
const SWIPE_LOCK_PX = 40;
const SWIPE_COMMIT_PX = 80;
/** The OS back-swipe lives in the left edge; a drag starting there is not ours. */
const EDGE_GUARD_PX = 24;


/** The outgoing slide's lifetime. Matches `deck-out-*` in leaf.css, plus a frame
 * so the last painted state is the animation's end rather than a snap. */
const EXIT_MS = 200;

/** Marks the outgoing slide inert.
 *
 * `inert` is set through a ref CALLBACK, and both halves of that matter. It is
 * not a prop because React 18 does not know the attribute and drops a boolean
 * one silently — the same class of bug as a class name that compiles to
 * nothing. And it is a callback rather than an effect on a ref object because
 * the outgoing element is a DIFFERENT node on every transition: an effect whose
 * dependencies never change runs once, at a moment when there is no outgoing
 * slide to mark, and then never again.
 *
 * It only ever needs setting — an outgoing slide is inert for its whole life —
 * and it sits on the element carrying `role="tabpanel"`, alongside
 * `tabIndex={-1}` and `aria-hidden`, so a transition never leaves two live
 * panels in the accessibility tree. */
function markInert(node: HTMLDivElement | null) {
  node?.setAttribute("inert", "");
}

export function Deck({
  slides,
  label,
  railHeader,
  itemNoun = "benefit",
  activeKey,
  onActiveKeyChange,
}: {
  slides: DeckSlide[];
  /** Names the rail for a screen reader — "Your benefits", not "Tabs". */
  label: string;
  /** One line pinned above the index, INSIDE the sticky container.
   *
   * For a fact that governs every slide rather than belonging to one — the
   * enrollment deck's running allowance. It has to travel with the rail rather
   * than sit above the deck, because on a phone the rail is the only part of
   * this component that stays on screen, and a budget you can only see by
   * scrolling back to the top is not a budget you can spend against.
   *
   * Keep it to a row. It is stuck to the top of the viewport on a phone, and
   * anything taller than the chips beneath it takes the page over. */
  railHeader?: ReactNode;
  /** What one slide IS, for the step buttons' screen-reader labels — "benefit"
   * on coverage, "step" on enrollment. Never rendered visually: the buttons
   * already print the destination's own name. */
  itemNoun?: string;
  /** Controlled: the member page drives this from the URL. Omit it entirely and
   * the deck holds its own state, which is what the broker preview needs. */
  activeKey?: string | null;
  onActiveKeyChange?: (key: string) => void;
}) {
  // The deck's own width decides its layout — see `useContainerWide`, which
  // holds the rationale and the synchronous first measure this used to own.
  const [measureDeck, wide] = useContainerWide(WIDE_AT);
  const stageRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const reduceMotion = useReducedMotion();

  const controlled = activeKey !== undefined;
  const [internalKey, setInternalKey] = useState<string | undefined>(undefined);
  const requested = controlled ? activeKey : internalKey;

  // An unknown key — a stale `?p=` from a bookmark, or a product dropped at
  // renewal — opens the first slide rather than an empty stage.
  let index = slides.findIndex((s) => s.key === requested);
  if (index < 0) index = 0;

  // Direction of travel, derived during render. Setting state here is the
  // sanctioned "adjust state when a prop changes" pattern: React re-runs this
  // component before committing, so the slide and its direction are always
  // rendered from the same pair.
  const [prevIndex, setPrevIndex] = useState(index);
  const [direction, setDirection] = useState(1);
  // The slide on its way out — AT MOST ONE, by construction. A newer change
  // replaces it rather than queueing behind it, which is the property that makes
  // rapid switching bounded: the reader can tap through all nine products and
  // the stage still holds two nodes.
  const [exiting, setExiting] = useState<{ index: number; dir: number } | null>(
    null,
  );
  if (prevIndex !== index) {
    const dir = index > prevIndex ? 1 : -1;
    setDirection(dir);
    setExiting({ index: prevIndex, dir });
    setPrevIndex(index);
  }

  const active = slides[index];

  // Removal on a known deadline. `exiting` is the dependency, so a second change
  // during an exit restarts the clock on the new outgoing slide rather than
  // leaving the first one behind.
  useEffect(() => {
    if (!exiting) return;
    const t = window.setTimeout(
      () => setExiting(null),
      reduceMotion ? 0 : EXIT_MS,
    );
    return () => window.clearTimeout(t);
  }, [exiting, reduceMotion]);

  // A slide that has left the list entirely (the year changed under the reader)
  // must not be rendered from a stale index.
  const exitingSlide =
    exiting && exiting.index < slides.length ? slides[exiting.index] : null;

  const select = useCallback(
    (key: string) => {
      if (!controlled) setInternalKey(key);
      onActiveKeyChange?.(key);
    },
    [controlled, onActiveKeyChange],
  );

  const step = useCallback(
    (delta: number) => {
      const next = slides[index + delta];
      if (next) select(next.key);
    },
    [index, select, slides],
  );

  // ── The travelling pill ────────────────────────────────────────────────
  const [pill, setPill] = useState<{
    x: number;
    y: number;
    w: number;
    h: number;
  } | null>(null);
  // **Only a change of SELECTION animates.** A change of the rail's own layout
  // — orientation, or the set of chips — moves every chip at once, and the pill
  // is then interpolating between two positions that describe different
  // layouts. On first paint that is guaranteed: `wide` starts false, so the
  // pill is measured against the horizontal rail one frame before the
  // ResizeObserver reports the vertical one, and it flies diagonally across the
  // page on load. Suppressed for a frame instead, so it simply appears where it
  // belongs.
  const [pillStill, setPillStill] = useState(true);
  const layoutSig = `${wide}|${slides.length}`;
  const lastLayoutSig = useRef<string | null>(null);
  const stillFrame = useRef<[number, number]>([0, 0]);

  /** Move without animating, then restore the transition a frame later so the
   * NEXT selection still travels. Two frames, not one: restoring the transition
   * in the same commit as the move is the case browsers disagree about. */
  const moveStill = useCallback(() => {
    setPillStill(true);
    // BOTH ids are cancelled. Storing only the outer one leaves the inner frame
    // alive once the outer has run, so a second layout change a frame later —
    // which is exactly the mount sequence, `layoutSig` then the observer
    // reporting `wide` — re-enabled the transition a frame early and let the
    // pill animate across the very reflow this exists to hide.
    cancelAnimationFrame(stillFrame.current[0]);
    cancelAnimationFrame(stillFrame.current[1]);
    stillFrame.current[0] = requestAnimationFrame(() => {
      stillFrame.current[1] = requestAnimationFrame(() => setPillStill(false));
    });
  }, []);
  useEffect(
    () => () => {
      cancelAnimationFrame(stillFrame.current[0]);
      cancelAnimationFrame(stillFrame.current[1]);
    },
    [],
  );

  useLayoutEffect(() => {
    const chip = chipRefs.current[index];
    const track = trackRef.current;
    if (!chip || !track) return;

    const place = () =>
      setPill({
        x: chip.offsetLeft,
        y: chip.offsetTop,
        w: chip.offsetWidth,
        h: chip.offsetHeight,
      });
    place();

    if (lastLayoutSig.current !== layoutSig) {
      lastLayoutSig.current = layoutSig;
      moveStill();
    }

    // The chips also reflow when a label wraps at a new width. Compared against
    // the last size rather than acting on every delivery, because a fresh
    // observer always fires once on observe — and this effect re-runs on every
    // selection, so an unguarded callback would cancel the very travel it
    // exists to preserve.
    let lastW = track.offsetWidth;
    let lastH = track.offsetHeight;
    const ro = new ResizeObserver(() => {
      if (track.offsetWidth === lastW && track.offsetHeight === lastH) return;
      lastW = track.offsetWidth;
      lastH = track.offsetHeight;
      moveStill();
      place();
    });
    ro.observe(track);
    return () => ro.disconnect();
  }, [index, layoutSig, moveStill]);

  // Bring the active chip into view in the horizontal rail. Manual rather than
  // `scrollIntoView`, which would also scroll the page to reach it.
  useEffect(() => {
    if (wide) return;
    const chip = chipRefs.current[index];
    const box = scrollRef.current;
    if (!chip || !box) return;
    const left = chip.offsetLeft;
    const right = left + chip.offsetWidth;
    const behavior: ScrollBehavior = reduceMotion ? "auto" : "smooth";
    if (left < box.scrollLeft + 8) {
      box.scrollTo({ left: Math.max(0, left - 8), behavior });
    } else if (right > box.scrollLeft + box.clientWidth - 8) {
      box.scrollTo({ left: right - box.clientWidth + 8, behavior });
    }
  }, [index, wide, reduceMotion]);

  // Land the reader at the top of the new schedule. Only when the stage has
  // already scrolled off the top — someone at the top of the page must not have
  // it twitch under them. `scroll-mt-*` on the stage keeps the sticky rail from
  // covering the heading it scrolls to.
  const scrollOnChange = useRef(false);
  useEffect(() => {
    if (!scrollOnChange.current) return;
    scrollOnChange.current = false;
    const el = stageRef.current;
    if (!el || el.getBoundingClientRect().top >= 0) return;
    el.scrollIntoView({
      block: "start",
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [index, reduceMotion]);
  useEffect(() => {
    scrollOnChange.current = true;
  }, [index]);

  // ── Keyboard: the tablist pattern ──────────────────────────────────────
  const onRailKeyDown = (e: React.KeyboardEvent) => {
    const last = slides.length - 1;
    let next: number | null = null;
    if (e.key === (wide ? "ArrowDown" : "ArrowRight")) next = index === last ? 0 : index + 1;
    else if (e.key === (wide ? "ArrowUp" : "ArrowLeft")) next = index === 0 ? last : index - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next === null) return;
    e.preventDefault();
    select(slides[next].key);
    chipRefs.current[next]?.focus();
  };

  // ── Swipe, with a direction lock ───────────────────────────────────────
  const drag = useRef<{ x: number; y: number; id: number; locked: boolean } | null>(
    null,
  );
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);

  const onPointerDown = (e: React.PointerEvent) => {
    // Primary pointer, primary button only. Without this a right-drag starts a
    // swipe, and a second finger overwrites the gesture in flight — the first
    // finger's moves are then dropped by the id check below and its release is
    // read as the start of a new drag.
    if (!e.isPrimary) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if ((e.target as HTMLElement).closest("button, a, input, select, textarea")) return;
    const box = stageRef.current?.getBoundingClientRect();
    if (box && e.clientX - box.left < EDGE_GUARD_PX) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId, locked: false };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || e.pointerId !== d.id) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.locked) {
      if (Math.abs(dx) < SWIPE_LOCK_PX) return;
      // Vertical scrolling always wins a tie: this page is read by scrolling,
      // and a horizontal gesture that steals an ambiguous drag makes it unusable.
      if (Math.abs(dx) < Math.abs(dy) * 2) {
        drag.current = null;
        return;
      }
      d.locked = true;
      setDragging(true);
      // Capture keeps the gesture alive if the finger leaves the stage, but it
      // throws on a pointer the element no longer owns — and an exception here
      // would skip the offset below, leaving the drag locked and visually dead
      // while still committing on release. The gesture works without it.
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      } catch {
        /* not capturable — carry on */
      }
    }
    const atEdge =
      (dx > 0 && index === 0) || (dx < 0 && index === slides.length - 1);
    const travel = dx - Math.sign(dx) * SWIPE_LOCK_PX;
    setDragX(travel * (atEdge ? 0.25 : 0.6));
  };

  /** A cancelled pointer is not a release. The browser cancels on a touch
   * cancel, an OS back-gesture or an incoming system UI — treating that as a
   * committed swipe changed the member's product without them ever letting go. */
  const cancelDrag = () => {
    drag.current = null;
    setDragging(false);
    setDragX(0);
  };

  const endDrag = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    // Clearing `dragging` first re-enables the transition, so an UNCOMMITTED
    // swipe settles back over 220ms instead of snapping. A committed one is
    // replaced by the incoming slide before that has time to read as motion.
    setDragging(false);
    setDragX(0);
    if (!d || !d.locked) return;
    const dx = e.clientX - d.x;
    if (dx <= -SWIPE_COMMIT_PX) step(1);
    else if (dx >= SWIPE_COMMIT_PX) step(-1);
  };

  const prev = slides[index - 1];
  const next = slides[index + 1];

  return (
    <div
      ref={measureDeck}
      className={cn("grid items-start gap-3.5", wide && "grid-cols-[236px_minmax(0,1fr)] gap-5")}
    >
      {/* ── The index ──────────────────────────────────────────────────── */}
      <div
        className={cn(
          glassSurface,
          "sticky top-2 z-10 overflow-hidden",
          // The pill shape belongs to a bare row of chips. Once a header sits
          // above them the container is a card, and a pill's radius would cut
          // the corners off its own first line.
          wide || railHeader ? "rounded-tile p-2" : "rounded-pill p-1.5",
        )}
      >
        {railHeader && (
          <div className="border-b border-hairline/75 px-1.5 pb-2 pt-1">
            {railHeader}
          </div>
        )}
        <div
          ref={scrollRef}
          className={cn(
            "leaf-deck-scroll relative",
            railHeader && "pt-1.5",
            !wide && "overflow-x-auto overflow-y-hidden",
          )}
        >
          <div
            ref={trackRef}
            role="tablist"
            aria-label={label}
            aria-orientation={wide ? "vertical" : "horizontal"}
            onKeyDown={onRailKeyDown}
            className={cn(
              "relative flex",
              wide ? "flex-col gap-px" : "w-max min-w-full gap-0.5",
            )}
          >
            {pill && (
              <span
                aria-hidden
                className={cn(
                  "leaf-deck-pill absolute left-0 top-0 z-0 bg-shade",
                  wide ? "rounded-control" : "rounded-pill",
                )}
                data-still={pillStill ? "" : undefined}
                style={{
                  transform: `translate(${pill.x}px, ${pill.y}px)`,
                  width: pill.w,
                  height: pill.h,
                }}
              />
            )}
            {slides.map((slide, i) => {
              const selected = i === index;
              return (
                <button
                  key={slide.key}
                  ref={(el) => (chipRefs.current[i] = el)}
                  type="button"
                  role="tab"
                  id={`deck-tab-${slide.key}`}
                  // Only the SELECTED tab points at a panel: the deck mounts one
                  // slide at a time, so every other chip's `aria-controls` named
                  // an element that is not in the document — a dangling
                  // reference is worse than none, since it tells a screen reader
                  // there is something to move to and then cannot deliver it.
                  aria-controls={selected ? `deck-panel-${slide.key}` : undefined}
                  aria-selected={selected}
                  aria-posinset={i + 1}
                  aria-setsize={slides.length}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => select(slide.key)}
                  className={cn(
                    "leaf-focus relative z-[1] flex min-h-11 shrink-0 items-center gap-2 rounded-pill",
                    "px-3.5 text-row transition-colors duration-200 ease-leaf hover:text-record",
                    selected ? "font-semibold text-record" : "font-medium text-label",
                    wide && "justify-start rounded-control text-left",
                  )}
                >
                  {/* Truncated on screen only: the button's text is whole in the
                      accessibility tree, and the slide beneath prints the full
                      name with its gloss. */}
                  <span className={cn("truncate", !wide && "max-w-44")}>
                    {slide.label}
                  </span>
                  {slide.mark &&
                    (wide ? (
                      <span className="leaf-label ml-auto shrink-0">
                        {slide.mark}
                      </span>
                    ) : (
                      <>
                        {/* Ink, not the action colour and not the pending
                            ramp: this is an annotation on an index, and both of
                            those already mean something else here (a brand fill
                            is a thing to press; the pending ramp is the shell's
                            "your window is open" dot). */}
                        <span
                          aria-hidden
                          className="size-1.5 shrink-0 rounded-pill bg-record"
                        />
                        <span className="sr-only">{slide.mark}</span>
                      </>
                    ))}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── The stage ──────────────────────────────────────────────────── */}
      <div>
        <div
          ref={stageRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={cancelDrag}
          // `scroll-mt-*` keeps the sticky rail from covering the heading the
          // correction below scrolls to — so it has to grow with the rail. With
          // a header the phone rail is ~108px tall against ~72px without one,
          // and at `scroll-mt-20` the incoming slide's own title landed under it.
          className={cn(
            "relative overflow-x-clip",
            railHeader ? "scroll-mt-32" : "scroll-mt-20",
          )}
        >
          {/* The outgoing slide is taken OUT OF FLOW, so the stage adopts the
              incoming height in one frame and nothing animates height. Springing
              between a four-row schedule and a sixty-nine-row one is where a
              component like this janks, and the scroll correction above puts the
              change off-screen anyway. */}
          {exitingSlide && (
            <div
              key={`${exitingSlide.key}-exit`}
              ref={markInert}
              role="tabpanel"
              aria-labelledby={`deck-tab-${exitingSlide.key}`}
              aria-hidden
              tabIndex={-1}
              className={cn(
                "absolute inset-x-0 top-0",
                exiting!.dir > 0 ? "leaf-deck-out-left" : "leaf-deck-out-right",
              )}
            >
              {exitingSlide.render()}
            </div>
          )}

          {/* Keyed on the slide, so React REMOUNTS it and the CSS entrance runs
              on mount — no two-frame dance to commit a start state, and the fill
              rules inside redraw their values on arrival for free. */}
          <div
            key={active.key}
            id={`deck-panel-${active.key}`}
            role="tabpanel"
            aria-labelledby={`deck-tab-${active.key}`}
            tabIndex={0}
            className={cn(
              "leaf-focus",
              direction > 0 ? "leaf-deck-in-right" : "leaf-deck-in-left",
            )}
          >
            {/* The drag lives on a WRAPPER, not on the animated element: a
                transform set here would be overwritten by the entrance
                animation, and the outgoing slide would travel with the finger
                as well. */}
            <div
              className={cn(
                "leaf-deck-drag",
                dragging && "leaf-deck-drag-active",
              )}
              style={dragX ? { transform: `translateX(${dragX}px)` } : undefined}
            >
              {active.render()}
            </div>
          </div>
        </div>

        {/* Named by destination, not bare arrows: it says where you are going
            and teaches the shape of the set. Neutral, never terracotta — these
            PICK a view rather than doing something to the member's record (the
            Do-vs-Pick Rule). */}
        <div className="mt-3.5 flex items-center gap-2.5">
          <DeckStep
            direction="prev"
            slide={prev}
            noun={itemNoun}
            onClick={() => step(-1)}
          />
          {/* Decorative: the chips carry `aria-posinset`/`aria-setsize`, so a
              screen reader already announces the position and a live region
              here would say it twice. */}
          <span aria-hidden className="shrink-0 px-1 text-2xs font-semibold text-label">
            {index + 1} of {slides.length}
          </span>
          <DeckStep
            direction="next"
            slide={next}
            noun={itemNoun}
            onClick={() => step(1)}
          />
        </div>
      </div>
    </div>
  );
}

function DeckStep({
  direction,
  slide,
  noun,
  onClick,
}: {
  direction: "prev" | "next";
  slide: DeckSlide | undefined;
  noun: string;
  onClick: () => void;
}) {
  const isNext = direction === "next";
  const Icon = isNext ? ChevronRight : ChevronLeft;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!slide}
      aria-label={
        slide
          ? `${isNext ? "Next" : "Previous"} ${noun}: ${slide.label}`
          : `${isNext ? "Next" : "Previous"} ${noun} unavailable`
      }
      className={cn(
        glassSurface,
        // The same single hover every card takes — a step button is a pane you
        // point at, so it thins like one. Its press is its own, tighter than a
        // card's, because a pill this size reads better with a small scale than
        // with a fill change alone.
        glassHover,
        "leaf-focus flex min-h-11 min-w-0 flex-1 items-center gap-1.5 rounded-pill px-3.5",
        "text-row font-semibold text-record active:scale-[0.99]",
        // Invisible rather than greyed at the ends: a permanently dead control
        // is furniture, and the counter beside it already says where you are.
        !slide && "pointer-events-none opacity-0",
        isNext && "justify-end",
      )}
    >
      {!isNext && <Icon className="size-4 shrink-0 text-label" aria-hidden />}
      <span className="truncate">{slide?.label ?? ""}</span>
      {isNext && <Icon className="size-4 shrink-0 text-label" aria-hidden />}
    </button>
  );
}
