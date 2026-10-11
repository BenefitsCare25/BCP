import type { PortalMessage } from "./portal";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A date the server wrote into prose ("22 Mar 2026"), as a Date the
 * translator formats in the reader's language. */
export function spokenDate(value: string): Date {
  const [day, month, year] = value.split(" ");
  return new Date(Date.UTC(Number(year), MONTHS.indexOf(month), Number(day)));
}

/** How a captured value is carried into the template: verbatim (the default),
 * as catalogued copy, as a spoken date, or as a ", "-separated list of copy. */
type Capture = "copy" | "date" | "list";
type Rule = readonly [pattern: RegExp, portalSource: string, captures?: Readonly<Record<number, Capture>>];

function captured(value: string, kind: Capture | undefined): unknown {
  if (kind === "copy") return { portalSource: value, values: [] };
  if (kind === "date") return spokenDate(value);
  if (kind === "list") {
    const items = value.split(", ");
    return { portalSource: items.map((_, index) => `{${index}}`).join(", "), values: items.map(item => captured(item, "copy")) };
  }
  return value;
}

/** Recognise only the application's fixed generated sentences. Captured names,
 * contact details, amounts and plan codes are interpolated without translation. */
export function generatedPortalCopy(source: string): PortalMessage | null {
  const rules: readonly Rule[] = [
    [/^We could not get an exchange rate for ([A-Z]{3}) on (\d{4}-\d{2}-\d{2})\. Your claim can still be sent — it will be converted by hand when it is reviewed\.$/, "We could not get an exchange rate for {0} on {1}. Your claim can still be sent — it will be converted by hand when it is reviewed."],
    [/^([A-Z]{3}) ([\d,.]+) is ([A-Z]{3}) ([\d,.]+), using the rate published on (\d{4}-\d{2}-\d{2}) — no rate is published for (\d{4}-\d{2}-\d{2})\.$/, "{0} {1} is {2} {3}, using the rate published on {4} — no rate is published for {5}."],
    [/^([A-Z]{3}) ([\d,.]+) is ([A-Z]{3}) ([\d,.]+) at the (\d{4}-\d{2}-\d{2}) rate\.$/, "{0} {1} is {2} {3} at the {4} rate."],
    [/^((?:[A-Z]{3}|S\$|\$))([\d,.]+) confirmed balance$/, "{0}{1} confirmed balance"],
    [/^(\d+(?:\.\d+)?) visits?$/, "{0} visits"],
    [/^(\d+(?:\.\d+)?) days?$/, "{0} days"],
    [/^Request failed \(HTTP (\d{3})\)$/, "Request failed (HTTP {0})"],
    [/^Spouse: your legal spouse, up to age (\d+) next birthday, not divorced or legally separated from you\.$/, "Spouse: your legal spouse, up to age {0} next birthday, not divorced or legally separated from you."],
    [/^Child: your child from 15 days old \(not in hospital confinement\) up to age (\d+) next birthday\.$/, "Child: your child from 15 days old (not in hospital confinement) up to age {0} next birthday."],
    [/^Over the age limit for (spouse|children) \((\d+) next birthday\)\.$/, "Over the age limit for {0} ({1} next birthday).", { 0: "copy" }],
    [/^Under the minimum age for (spouse|children) \((\d+) next birthday\)\.$/, "Under the minimum age for {0} ({1} next birthday).", { 0: "copy" }],
    [/^Not eligible for ([A-Z0-9, ]+) \(outside that plan's age limit\)\.$/, "Not eligible for {0} (outside that plan's age limit)."],
    [/^For any queries on this form, please contact (.+?) at (.+)\.$/, "For any queries on this form, please contact {0} at {1}."],
    [/^That upload is too large — uploads are limited to (\d+(?:\.\d+)?) MB at a time\. Use smaller files, or send fewer at once, and try again\.$/, "That upload is too large — uploads are limited to {0} MB at a time. Use smaller files, or send fewer at once, and try again."],
    [/^That file is too large — the limit is (\d+(?:\.\d+)?) MB\. Choose a smaller file and try again\.$/, "That file is too large — the limit is {0} MB. Choose a smaller file and try again."],
    [/^The document scanner is busy, so your file wasn't uploaded\. Try again in about (\d+) seconds\.$/, "The document scanner is busy, so your file wasn't uploaded. Try again in about {0} seconds."],
    [/^The document scanner is busy, so your file wasn't uploaded\. Try again in about (\d+) minutes?\.$/, "The document scanner is busy, so your file wasn't uploaded. Try again in about {0} minutes."],
    [/^Your access to this portal ended on (\d{1,2} [A-Z][a-z]{2} \d{4})\. Contact your HR team if you still need something from your record\.$/, "Your access to this portal ended on {0}. Contact your HR team if you still need something from your record.", { 0: "date" }],
    [/^Password must be at least (\d+) characters\.$/, "Password must be at least {0} characters."],
    [/^PDFs must contain between 1 and (\d+) pages\.$/, "PDFs must contain between 1 and {0} pages."],
    [/^Invoice (.+) has already been claimed\. Check the number against your receipt — if it's right, that claim is already with us and there's nothing more to send\.$/, "Invoice {0} has already been claimed. Check the number against your receipt — if it's right, that claim is already with us and there's nothing more to send."],
    [/^([A-Z0-9][\w-]*) coverage period$/, "{0} coverage period"],
    [/^The incurred date must fall within the (.+) \((\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})\)\.$/, "The incurred date must fall within the {0} ({1} to {2}).", { 0: "copy" }],
    [/^The claim submission window for this (.+) closed on (\d{4}-\d{2}-\d{2}) \(period end \+ (\d+) days grace\)\.$/, "The claim submission window for this {0} closed on {1} (period end + {2} days grace).", { 0: "copy" }],
    [/^Your cover ended on (\d{4}-\d{2}-\d{2}), before this (.+) began — there is nothing to claim against here\.$/, "Your cover ended on {0}, before this {1} began — there is nothing to claim against here.", { 1: "copy" }],
    [/^Missing required documents: (.+)\.$/, "Missing required documents: {0}.", { 0: "list" }],
    [/^([A-Z]{3}) ([\d,.]+) converts to ([A-Z]{3}) ([\d,.]+)\. Confirm the converted amount before sending this claim\.$/, "{0} {1} converts to {2} {3}. Confirm the converted amount before sending this claim."],
    [/^A (.+) claim has already been sent — there is nothing more to send\.$/, "A {0} claim has already been sent — there is nothing more to send."],
    [/^You have (\d+) questions waiting on us\. We'll answer those first — add anything else to one of them and we'll pick it up there\.$/, "You have {0} questions waiting on us. We'll answer those first — add anything else to one of them and we'll pick it up there."],
    [/^Unknown topic '(.+)'\.$/, "Unknown topic '{0}'."],
    [/^Unsupported file type: (.+)\. Allowed: (\[.*\])$/, "Unsupported file type: {0}. Allowed: {1}"],
    // Claim intake and coverage guards (claim_intake.py, claims.assert_coverage_claimable).
    [/^Upload at most (\d+) documents at a time\.$/, "Upload at most {0} documents at a time."],
    [/^'(.+)' is not a recognised document type\.$/, "'{0}' is not a recognised document type."],
    [/^'(.*)' is not a supported claim currency\.$/, "'{0}' is not a supported claim currency."],
    [/^'(.+)' is not a valid sub-type for (.+) claims\.$/, "'{0}' is not a valid sub-type for {1} claims.", { 0: "copy" }],
    [/^(.+) claims do not take a claim sub-type\.$/, "{0} claims do not take a claim sub-type."],
    [/^(.+) claims do not take a referral letter\.$/, "{0} claims do not take a referral letter."],
    [/^(.+) claims do not take a visit type\.$/, "{0} claims do not take a visit type."],
    [/^(.+) claims aren't submitted through the portal — please contact your broker\.$/, "{0} claims aren't submitted through the portal — please contact your broker."],
    [/^'(.+)' is not a claimable flex benefit for you\.$/, "'{0}' is not a claimable flex benefit for you.", { 0: "copy" }],
    [/^Your (.+?) plan does not include (.+) cover\.$/, "Your {0} plan does not include {1} cover.", { 1: "copy" }],
    [/^You have no (.+) coverage to claim against\.$/, "You have no {0} coverage to claim against."],
    [/^You are eligible for (.+) but not enrolled, so there is no cover to claim against yet\.$/, "You are eligible for {0} but not enrolled, so there is no cover to claim against yet."],
    [/^Your (.+) cover is still being set up — please contact your HR team\.$/, "Your {0} cover is still being set up — please contact your HR team."],
    [/^'(.+)' is not a benefit item on your (.+) schedule\.$/, "'{0}' is not a benefit item on your {1} schedule.", { 0: "copy" }],
    [/^Your (.+) coverage does not extend to dependants\.$/, "Your {0} coverage does not extend to dependants."],
    [/^This dependant is not covered under your (.+) plan\.$/, "This dependant is not covered under your {0} plan."],
    [/^The submission window for (.+) closed on (\d{4}-\d{2}-\d{2})\.$/, "The submission window for {0} closed on {1}."],
    // Enrolment guards (enrollment_elections.py, enrollment_validation.py).
    [/^Product '(.+)' is not configured in this policy year\.$/, "Product '{0}' is not configured in this policy year."],
    [/^Product '(.+)' is compulsory and cannot be declined\.$/, "Product '{0}' is compulsory and cannot be declined."],
    [/^Product '(.+)' is not in this enrolment period's scope\.$/, "Product '{0}' is not in this enrolment period's scope."],
    [/^Product '(.+)' has no configured plans to elect\.$/, "Product '{0}' has no configured plans to elect."],
    [/^Plan '(.+)' is not available for product '(.+)'\.$/, "Plan '{0}' is not available for product '{1}'."],
    [/^Plan '(.+)' is not in this member's cohort for product '(.+)'\.$/, "Plan '{0}' is not in this member's cohort for product '{1}'."],
    [/^Plan '(.+)' maps to several tiers for '(.+)'; specify a tier_category_id\.$/, "Plan '{0}' maps to several tiers for '{1}'; specify a tier_category_id."],
    [/^Tier is not electable for product '(.+)'\.$/, "Tier is not electable for product '{0}'."],
    [/^Tier maps to several plans for '(.+)'; specify a plan_code\.$/, "Tier maps to several plans for '{0}'; specify a plan_code."],
    [/^No baseline tier for product '(.+)'\.$/, "No baseline tier for product '{0}'."],
    [/^Dependants are inactive or do not belong to this employee: (.+)\.$/, "Dependants are inactive or do not belong to this employee: {0}."],
    [/^'(.+)' is not an electable (spouse|child) dependant option level of this product\.$/, "'{0}' is not an electable {1} dependant option level of this product.", { 1: "copy" }],
    [/^days must be between ([\d.]+) and ([\d.]+) for 'buy' for '(.+)'\.$/, "days must be between {0} and {1} for 'buy' for '{2}'."],
    [/^days must be between ([\d.]+) and ([\d.]+) for 'sell' for '(.+)'\.$/, "days must be between {0} and {1} for 'sell' for '{2}'."],
    [/^days must be between ([\d.]+) and ([\d.]+) for 'buy'\.$/, "days must be between {0} and {1} for 'buy'."],
    [/^days must be between ([\d.]+) and ([\d.]+) for 'sell'\.$/, "days must be between {0} and {1} for 'sell'."],
    [/^days must be in increments of ([\d.]+)\.$/, "days must be in increments of {0}."],
    [/^Unknown leave action '(.*)'\.$/, "Unknown leave action '{0}'."],
  ];
  for (const [pattern, portalSource, captures] of rules) {
    const match = pattern.exec(source);
    if (!match) continue;
    return { portalSource, values: match.slice(1).map((value, index) => captured(value, captures?.[index])) };
  }
  return null;
}
