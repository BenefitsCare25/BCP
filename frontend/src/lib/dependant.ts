/** Reading a dependant's identity out of roster `attribute_values`.
 *
 * Rosters are uploaded, not authored, so the same field arrives under several
 * spellings. These helpers are shared by every surface that names a dependant
 * (the member's list, the enrollment pickers, the broker's employee-view
 * preview) so a person can't be called one thing on one screen and another
 * somewhere else. */
import type { Dependant } from "@/types";

const NAME_KEYS = ["name", "dependant_name", "full_name"];
const REL_KEYS = ["relationship", "relation", "rel", "dependant_type", "type"];
const DOB_KEYS = ["dob", "date_of_birth", "birth_date", "birthdate"];

function attr(dep: Dependant, keys: string[]): string | null {
  for (const key of keys) {
    const value = dep.attribute_values[key];
    if (value !== null && value !== undefined && value !== "") {
      const text = String(value).trim();
      if (text) return text;
    }
  }
  return null;
}

export function dependantName(dep: Dependant): string | null {
  return attr(dep, NAME_KEYS);
}

export function dependantRelationship(dep: Dependant): string | null {
  return attr(dep, REL_KEYS);
}

/**
 * Roster dates arrive as "1986-04-29 00:00:00"; the time is never real, so the
 * date half is taken here — and ONLY here.
 *
 * This split used to live in the shared reader, which meant it ran over names
 * and relationships too: "Mary Anne" rendered as "Mary", and any name starting
 * with T ("Tan Wei Ling") split at index 0 and produced an EMPTY STRING — which
 * then passed `name ?? "Family member"` and rendered a mount with a blank
 * heading. Keep it in the date accessor.
 */
export function dependantDob(dep: Dependant): string | null {
  const raw = attr(dep, DOB_KEYS);
  return raw ? raw.split(/[ T]/)[0] : null;
}
