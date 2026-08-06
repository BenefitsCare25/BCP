import { create } from "zustand";

export type NotificationTone = "error" | "warning" | "info";

export interface AppNotification {
  /** Dedupe key — the message itself. One backend fault fails every in-flight
   * query with the SAME message; those are one event, not N. */
  id: string;
  message: string;
  tone: NotificationTone;
  /** How many times this exact message has fired since it was last cleared. */
  count: number;
  /** Epoch ms of the most recent occurrence. */
  at: number;
  read: boolean;
}

/** Keep the log bounded — anything older is noise, and an unbounded list is a
 * memory leak on a long-lived session. */
const MAX_ITEMS = 30;

interface NotificationState {
  items: AppNotification[];
  /** Bumped on every genuinely NEW arrival (not a repeat), so the bell can
   * animate once without re-firing for duplicates. */
  lastArrivalId: string | null;
  notify: (input: { message: string; tone?: NotificationTone }) => void;
  markAllRead: () => void;
  dismiss: (id: string) => void;
  clear: () => void;
}

export const useNotifications = create<NotificationState>()((set) => ({
  items: [],
  lastArrivalId: null,
  notify: ({ message, tone = "error" }) =>
    set((s) => {
      const text = message.trim() || "Something went wrong.";
      const now = Date.now();
      const existing = s.items.find((n) => n.id === text);
      if (existing) {
        // A repeat updates the entry in place and keeps it at the top — it
        // never adds a second row and never re-triggers the arrival animation.
        const updated: AppNotification = {
          ...existing,
          count: existing.count + 1,
          at: now,
          read: false,
        };
        return {
          items: [updated, ...s.items.filter((n) => n.id !== text)],
          lastArrivalId: s.lastArrivalId,
        };
      }
      const item: AppNotification = {
        id: text,
        message: text,
        tone,
        count: 1,
        at: now,
        read: false,
      };
      return {
        items: [item, ...s.items].slice(0, MAX_ITEMS),
        lastArrivalId: text,
      };
    }),
  markAllRead: () =>
    set((s) => ({ items: s.items.map((n) => ({ ...n, read: true })) })),
  dismiss: (id) => set((s) => ({ items: s.items.filter((n) => n.id !== id) })),
  clear: () => set({ items: [], lastArrivalId: null }),
}));

/** Push a notification from outside React (the query client, api layer). */
export function notify(input: {
  message: string;
  tone?: NotificationTone;
}): void {
  useNotifications.getState().notify(input);
}
