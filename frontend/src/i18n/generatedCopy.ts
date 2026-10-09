import type { PortalMessage } from "./portal";

/** Recognise only the application's fixed generated sentences. Captured names,
 * contact details, amounts and plan codes are interpolated without translation. */
export function generatedPortalCopy(source: string): PortalMessage | null {
  const rules: readonly [RegExp, string][] = [
    [/^(\d+(?:\.\d+)?) visits?$/, "{0} visits"],
    [/^(\d+(?:\.\d+)?) days?$/, "{0} days"],
    [/^Request failed \(HTTP (\d{3})\)$/, "Request failed (HTTP {0})"],
    [/^Spouse: your legal spouse, up to age (\d+) next birthday, not divorced or legally separated from you\.$/, "Spouse: your legal spouse, up to age {0} next birthday, not divorced or legally separated from you."],
    [/^Child: your child from 15 days old \(not in hospital confinement\) up to age (\d+) next birthday\.$/, "Child: your child from 15 days old (not in hospital confinement) up to age {0} next birthday."],
    [/^Over the age limit for (spouse|children) \((\d+) next birthday\)\.$/, "Over the age limit for {0} ({1} next birthday)."],
    [/^Under the minimum age for (spouse|children) \((\d+) next birthday\)\.$/, "Under the minimum age for {0} ({1} next birthday)."],
    [/^Not eligible for ([A-Z0-9, ]+) \(outside that plan's age limit\)\.$/, "Not eligible for {0} (outside that plan's age limit)."],
    [/^For any queries on this form, please contact (.+?) at (.+)\.$/, "For any queries on this form, please contact {0} at {1}."],
    [/^That upload is too large — uploads are limited to (\d+(?:\.\d+)?) MB at a time\. Use smaller files, or send fewer at once, and try again\.$/, "That upload is too large — uploads are limited to {0} MB at a time. Use smaller files, or send fewer at once, and try again."],
    [/^That file is too large — the limit is (\d+(?:\.\d+)?) MB\. Choose a smaller file and try again\.$/, "That file is too large — the limit is {0} MB. Choose a smaller file and try again."],
    [/^The document scanner is busy, so your file wasn't uploaded\. Try again in about (\d+) seconds\.$/, "The document scanner is busy, so your file wasn't uploaded. Try again in about {0} seconds."],
    [/^The document scanner is busy, so your file wasn't uploaded\. Try again in about (\d+) minutes?\.$/, "The document scanner is busy, so your file wasn't uploaded. Try again in about {0} minutes."],
  ];
  for (const [pattern, portalSource] of rules) {
    const match = pattern.exec(source);
    if (!match) continue;
    const values: unknown[] = match.slice(1);
    if (portalSource.startsWith("Over the") || portalSource.startsWith("Under the")) values[0] = { portalSource: values[0], values: [] };
    return { portalSource, values };
  }
  return null;
}
