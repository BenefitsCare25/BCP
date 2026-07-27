import { useEffect, useRef, useState } from "react";
import { AlertCircle, Bell, Info, TriangleAlert, X } from "lucide-react";
import {
  useNotifications,
  type AppNotification,
  type NotificationTone,
} from "@/stores/notifications";
import { cn } from "@/lib/cn";

const TONE_ICON: Record<NotificationTone, typeof AlertCircle> = {
  error: AlertCircle,
  warning: TriangleAlert,
  info: Info,
};

const TONE_CLASS: Record<NotificationTone, string> = {
  error: "text-error",
  warning: "text-warn",
  info: "text-info",
};

/** "just now" / "4m ago" / "2h ago" / a date. Relative reads better than a
 * timestamp for a log you glance at seconds after the event. */
function ago(at: number): string {
  const secs = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(at).toLocaleDateString();
}

/**
 * Top-bar notification centre. Replaces the stack of error toasts that used to
 * cover the navigation: every message lands here ONCE (repeats bump a counter
 * on the existing row) and stays until dismissed, so nothing is missed and
 * nothing blocks a click.
 */
export function NotificationBell() {
  const items = useNotifications((s) => s.items);
  const lastArrivalId = useNotifications((s) => s.lastArrivalId);
  const markAllRead = useNotifications((s) => s.markAllRead);
  const dismiss = useNotifications((s) => s.dismiss);
  const clear = useNotifications((s) => s.clear);

  const [open, setOpen] = useState(false);
  const [pulse, setPulse] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const unread = items.filter((n) => !n.read).length;

  // Draw the eye once per NEW message (repeats don't re-fire) — the bell is
  // quieter than a toast, so a silent badge alone would be too easy to miss.
  useEffect(() => {
    if (!lastArrivalId) return;
    setPulse(true);
    const t = window.setTimeout(() => setPulse(false), 900);
    return () => window.clearTimeout(t);
  }, [lastArrivalId]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) markAllRead(); // opening IS the acknowledgement
  };

  return (
    <div ref={panelRef} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-label={
          unread > 0 ? `Notifications (${unread} unread)` : "Notifications"
        }
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          "relative flex size-8 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          open
            ? "bg-accent text-primary"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <Bell
          className={cn("size-[18px]", pulse && "notification-pulse")}
          strokeWidth={1.75}
        />
        {unread > 0 && (
          <span
            aria-hidden="true"
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-error px-1 text-[10px] font-semibold leading-none text-error-foreground"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="absolute right-0 top-10 z-50 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-md border border-border bg-card shadow-md"
        >
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-sm font-medium text-foreground">
              Notifications
            </span>
            {items.length > 0 && (
              <button
                type="button"
                onClick={clear}
                className="text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded"
              >
                Clear all
              </button>
            )}
          </div>

          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">
              No notifications
            </p>
          ) : (
            <ul className="max-h-[22rem] overflow-y-auto">
              {items.map((n) => (
                <NotificationRow key={n.id} item={n} onDismiss={dismiss} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function NotificationRow({
  item,
  onDismiss,
}: {
  item: AppNotification;
  onDismiss: (id: string) => void;
}) {
  const Icon = TONE_ICON[item.tone];
  return (
    <li className="group flex items-start gap-2.5 border-b border-border px-3 py-2.5 last:border-b-0">
      <Icon
        className={cn("mt-0.5 size-4 shrink-0", TONE_CLASS[item.tone])}
        strokeWidth={2}
      />
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm text-foreground">{item.message}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {ago(item.at)}
          {item.count > 1 && ` · ${item.count} times`}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="Dismiss notification"
        className="shrink-0 rounded p-0.5 text-subtle opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 group-hover:opacity-100"
      >
        <X className="size-3.5" />
      </button>
    </li>
  );
}
