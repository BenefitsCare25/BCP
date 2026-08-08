/** "Your cover has ended" — the one thing a leaver's portal has to say.
 *
 * Without it the surface just starts refusing: the card tab 403s, the enrolment
 * tab disappears, and nothing on screen connects any of that to the fact that
 * their cover ended. A member reads that as the app being broken and calls
 * their broker.
 *
 * Everything here comes from the SERVED `PortalAccess` — the dates, and (via
 * `PortalShell`) which tabs are still there. Nothing re-derives what a state
 * implies; that table is `services/member_access.py`'s.
 */
import type { PortalAccess } from "@/api/portal";
import { formatDay } from "./leaf/date";

/** The sentence for a state, or null when there is nothing to say.
 *
 * `unknown` says nothing on purpose: it means we could not find their roster
 * row — usually a new benefit year that has not been uploaded — and guessing
 * out loud there would tell someone they had left when they had not.
 *
 * `active` says nothing UNLESS a last day is served with it. That combination
 * is a member on notice: still fully covered, and everything ends on a date the
 * server already knows. Both dates were served on every response and rendered
 * by nobody, so the tabs and the panel card simply vanished overnight with no
 * warning on any surface.
 */
export function accessNotice(access: PortalAccess | undefined): string | null {
  if (!access) return null;
  const ended = access.last_day ? formatDay(access.last_day) : null;
  const until = access.access_ends_on ? formatDay(access.access_ends_on) : null;

  switch (access.state) {
    case "active":
      if (!ended) return null;
      return [
        `Your last day of cover is ${ended}.`,
        until
          ? `You keep everything until then, and can send us claims for treatment on or before that date until ${until}.`
          : "You keep everything until then.",
      ].join(" ");
    case "run_off":
      return [
        ended ? `Your cover ended on ${ended}.` : "Your cover has ended.",
        // The one thing they can still act on, and the deadline for it. Said
        // in terms of the TREATMENT date, not the claim date — those are
        // different dates and only the first is bounded by their last day.
        ended
          ? `You can still send us claims for treatment on or before that date${
              until ? `, until ${until}` : ""
            }.`
          : `You can still send us claims for treatment during your cover${
              until ? `, until ${until}` : ""
            }.`,
      ].join(" ");
    case "settling":
      return (
        "Your cover has ended and the window for new claims has closed. " +
        "You can still read your claims and reply to anything we've asked you about."
      );
    case "ended":
      return until
        ? `Your access to this portal ended on ${until}.`
        : "Your access to this portal has ended.";
    default:
      return null;
  }
}

export function AccessNotice({ access }: { access: PortalAccess | undefined }) {
  const notice = accessNotice(access);
  if (!notice) return null;
  return (
    <p
      role="status"
      className="mb-4 rounded-tile border border-hairline bg-shade px-4 py-3 text-row text-record"
    >
      {notice}
    </p>
  );
}
