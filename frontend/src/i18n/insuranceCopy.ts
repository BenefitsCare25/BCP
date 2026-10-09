/** Compose only recognised policy phrases. A partly recognised sentence is
 * left intact rather than changing insurer names or user-entered prose. */
export function translateInsuranceText(
  source: string,
  lookup: (source: string) => string | undefined,
  depth = 0,
): string | undefined {
  if (depth > 5) return undefined;
  const known = lookup(source);
  if (known !== undefined) return known;
  const fill = (key: string, value: string) => lookup(key)?.replace("{0}", value);
  const plan = /^Plan\s*(\d+|[A-Z](?:\d+)?)$/i.exec(source.trim());
  if (plan) return fill("Plan {0}", plan[1]);
  const policy = /^Policy\s+(\d[\w/-]*)$/i.exec(source.trim());
  if (policy) return fill("Policy {0}", policy[1]);
  const reference = /^Refer to\s+(\d+[a-z]?(?:\s*\/\s*\d+[a-z]?)*)$/i.exec(source.trim());
  if (reference) return fill("Refer to {0}", reference[1]);
  const count = /^(\d+(?:\.\d+)?)\s+(days?|visits?)$/i.exec(source.trim());
  if (count) return fill(/day/i.test(count[2]) ? "{0} days" : "{0} visits", count[1]);
  const capped = /^(.+), up to S\$([\d,.]+)$/i.exec(source.trim());
  if (capped) {
    const basis = translateInsuranceText(capped[1], lookup, depth + 1);
    if (basis !== undefined) return lookup("{0}, up to S${1}")?.replace("{0}", basis).replace("{1}", capped[2]);
  }
  const salary = /^(\d+(?:\.\d+)?)\s*(?:x|×|times)\s+(.+)$/i.exec(source.trim());
  if (salary && lookup(salary[2]) !== undefined) return lookup("{0} × {1}")?.replace("{0}", salary[1]).replace("{1}", lookup(salary[2])!);
  const relative = /^(\d+(?:\.\d+)?)% of (GTL|GPA|GCI|GDD)$/i.exec(source.trim());
  if (relative) return lookup("{0}% of {1}")?.replace("{0}", relative[1]).replace("{1}", relative[2]);

  // Qualifiers and split schedule headings are supplied separately by the API.
  for (const separator of [" — ", " · "]) {
    if (!source.includes(separator)) continue;
    const pieces = source.split(separator).map(part => translateInsuranceText(part.trim(), lookup, depth + 1));
    if (pieces.every((part): part is string => part !== undefined)) return pieces.join(separator === " · " ? "；" : "－");
  }
  const qualified = /^([^()]+)((?:\s*\([^()]*\))+)$/.exec(source.trim());
  if (qualified) {
    const head = translateInsuranceText(qualified[1].trim(), lookup, depth + 1);
    const notes = [...qualified[2].matchAll(/\(([^()]*)\)/g)]
      .map(match => translateInsuranceText(match[1].trim(), lookup, depth + 1));
    if (head !== undefined && notes.every((note): note is string => note !== undefined)) return `${head}（${notes.join("；")}）`;
  }
  return undefined;
}
