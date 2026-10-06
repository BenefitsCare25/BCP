/** Shared vocabulary for an enrolment period — one place that decides how a
 * phase, a deadline and a member's status read, so the Overview, the Members
 * tab and the close dialog can never describe the same period two ways. */
import type {
  EnrollmentStatus,
  EnrollmentWindow,
  WindowPhase,
} from "@/api/enrollment";
import { useEffect, useState } from "react";
import { parseServerDate } from "@/lib/format";

type BadgeVariant = "default" | "primary" | "info" | "good" | "warn" | "error" | "outline";

export const PHASE_META: Record<WindowPhase, { label: string; badge: BadgeVariant }> = {
  draft: { label: "Draft", badge: "outline" },
  scheduled: { label: "Scheduled", badge: "info" },
  open: { label: "Open", badge: "good" },
  overdue: { label: "Deadline passed", badge: "warn" },
  closed: { label: "Closed", badge: "default" },
};

/** The period's phase NOW. Derived from the dates on every render rather than
 * trusting the server's `phase`, which is only true at the moment it was
 * fetched: a page opened at 16:58 would otherwise keep offering edits past a
 * 17:00 deadline and 409 on every save. Pair with `useNow` so the page
 * re-renders across the boundary without a refetch. */
export function phaseOf(w: EnrollmentWindow, now = Date.now()): WindowPhase {
  if (w.status !== "open") return w.status;
  if (now < parseServerDate(w.opens_at).getTime()) return "scheduled";
  if (now > parseServerDate(w.closes_at).getTime()) return "overdue";
  return "open";
}

/** Re-render once a minute so a phase derived from the clock stays true. */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** A period is "live" from the moment it opens until it is closed. */
export function isLive(w: EnrollmentWindow): boolean {
  return w.status === "open";
}

/** The period the Overview leads with: a live one first (the soonest to
 * close), else the newest draft. */
export function focusPeriod(
  windows: EnrollmentWindow[] | undefined,
): EnrollmentWindow | undefined {
  const list = windows ?? [];
  const live = list
    .filter(isLive)
    .sort((a, b) => a.closes_at.localeCompare(b.closes_at));
  return live[0] ?? list.find((w) => w.status === "draft");
}

const DAY_MS = 86_400_000;

export function fmtWhen(iso: string): string {
  const d = parseServerDate(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function dayCount(ms: number): string {
  const days = Math.round(ms / DAY_MS);
  if (days <= 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

/** The one sentence about time a broker needs for this period. */
export function deadlineSentence(w: EnrollmentWindow, now = Date.now()): string {
  const opens = parseServerDate(w.opens_at).getTime();
  const closes = parseServerDate(w.closes_at).getTime();
  switch (phaseOf(w, now)) {
    case "draft":
      return `Planned for ${fmtWhen(w.opens_at)} – ${fmtWhen(w.closes_at)}`;
    case "scheduled":
      return `Members can start ${fmtWhen(w.opens_at)} (in ${dayCount(opens - now)})`;
    case "open": {
      const left = closes - now;
      return left < DAY_MS
        ? `Closes to members today at ${parseServerDate(w.closes_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
        : `Closes to members ${fmtWhen(w.closes_at)} (${dayCount(left)} left)`;
    }
    case "overdue": {
      const ago = dayCount(now - closes);
      return `Deadline passed ${fmtWhen(w.closes_at)} (${ago === "today" ? "today" : `${ago} ago`})`;
    }
    case "closed":
      return `Ran ${fmtWhen(w.opens_at)} – ${fmtWhen(w.closes_at)}`;
  }
}

/** `datetime-local` value (local wall time) for an ISO / server timestamp. */
export function toLocalInput(iso: string | Date): string {
  const d = typeof iso === "string" ? parseServerDate(iso) : iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO string from a `datetime-local` value, or null when blank/invalid. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export interface StatusMeta {
  label: string;
  /** Plain-language gloss: what this status means for the member. */
  gloss: string;
  /** Token-based fill for the progress bar segment + legend dot. */
  fill: string;
  text: string;
}

// Ordered as the work flows — the progress bar reads left to right in it.
export const STATUS_ORDER: EnrollmentStatus[] = [
  "confirmed",
  "deemed",
  "submitted",
  "returned",
  "in_progress",
  "declined",
  "not_started",
];

export const STATUS_META: Record<EnrollmentStatus, StatusMeta> = {
  returned: { label: "Needs correction", gloss: "Returned to the employee to correct and sign again", text: "text-warn", fill: "bg-warn" },
  confirmed: {
    label: "Confirmed",
    gloss: "Choices are live coverage.",
    fill: "bg-good",
    text: "text-good",
  },
  deemed: {
    label: "Defaulted",
    gloss: "Finalised by the period's default at close.",
    fill: "bg-subtle",
    text: "text-muted-foreground",
  },
  submitted: {
    label: "Submitted",
    gloss: "Sent — waiting for a broker to confirm.",
    fill: "bg-info",
    text: "text-info",
  },
  in_progress: {
    label: "Saved, not sent",
    gloss: "Has changes saved but never submitted.",
    fill: "bg-warn",
    text: "text-warn",
  },
  declined: {
    label: "Declined all",
    gloss: "Chose to decline cover.",
    fill: "bg-error",
    text: "text-error",
  },
  not_started: {
    label: "Not started",
    gloss: "Hasn't touched their selection.",
    fill: "bg-border-strong",
    text: "text-muted-foreground",
  },
};

export const DEFAULT_BEHAVIOR_TEXT = {
  deemed_keep_current: "keep their current plans",
  deemed_decline: "decline voluntary cover (compulsory cover is never removed)",
} as const;
