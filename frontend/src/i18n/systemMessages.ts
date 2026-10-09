import type { ClaimMessage } from "@/api/portalMessages";
import { portalMessage, type PortalTranslator } from "./portal";

const day = "(\\d{2} [A-Z][a-z]{2} \\d{4})";
const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function date(value: string) {
  const [d, m, y] = value.split(" ");
  return new Date(Date.UTC(Number(y), months.indexOf(m), Number(d)));
}

/** Translate only the system's known snapshot paragraphs. Broker/member prose
 * and historical figures stay intact; never reconstruct a notice from today's claim. */
export function systemClaimBody(message: Pick<ClaimMessage, "author_type" | "event" | "body">, pt: PortalTranslator): string {
  if (message.author_type !== "system" || !message.event || pt("We have your claim") === "We have your claim") return message.body;
  const paragraphs = message.body.split("\n\n");
  const first = paragraphs[0];
  let match: RegExpExecArray | null;
  switch (message.event) {
    case "submitted":
      match = new RegExp(`^Your (.+?) claim for (.+?) on ${day} is with us\\. You don't need to send it again — if we need anything else, it will appear here\\.$`).exec(first);
      if (match) paragraphs[0] = pt("Your {0} claim for {1} on {2} is with us. You don't need to send it again — if we need anything else, it will appear here.", [pt(match[1]), match[2], date(match[3])]);
      break;
    case "approved":
      match = new RegExp(`^Your (.+?) claim for ${day} has been approved(?: for (.+?))?\\.$`).exec(first);
      if (match) paragraphs[0] = pt("Your {0} claim for {1} has been approved{2}.", [pt(match[1]), date(match[2]), match[3] ? portalMessage(" for {0}", [match[3]]) : ""]);
      break;
    case "rejected":
      match = new RegExp(`^We weren't able to approve your (.+?) claim for ${day}\\.$`).exec(first);
      if (match) paragraphs[0] = pt("We weren't able to approve your {0} claim for {1}.", [pt(match[1]), date(match[2])]);
      break;
    case "paid":
      match = new RegExp(`^Your (.+?) claim for ${day} has been paid(?: — (.+?))?(?: on ${day})?\\.$`).exec(first);
      if (match) paragraphs[0] = pt("Your {0} claim for {1} has been paid{2}{3}.", [pt(match[1]), date(match[2]), match[3] ? ` — ${match[3]}` : "", match[4] ? portalMessage(" on {0}", [date(match[4])]) : ""]);
      break;
    case "amended":
      match = new RegExp(`^Your (.+?) claim for ${day} has been updated\\.$`).exec(first);
      if (match) paragraphs[0] = pt("Your {0} claim for {1} has been updated.", [pt(match[1]), date(match[2])]);
      break;
    case "needs_info":
      match = new RegExp(`^Before we can finish your (.+?) claim for ${day}, we need a little more from you\\.$`).exec(first);
      if (match) paragraphs[0] = pt("Before we can finish your {0} claim for {1}, we need a little more from you.", [pt(match[1]), date(match[2])]);
      break;
  }
  const footer = paragraphs.at(-1);
  const footers = ["If you think something has been missed, reply here and we'll take another look.", "It can take a few working days to reach your account.", "Open the claim to send it again when you're ready.", "We'll carry on with it as it now stands — there's nothing more you need to send.", "Open the claim to add what's missing, then send it again."];
  if (paragraphs.length > 1 && footer && footers.includes(footer)) paragraphs[paragraphs.length - 1] = pt(footer);
  return paragraphs.join("\n\n");
}
