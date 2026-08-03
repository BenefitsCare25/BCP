/** The plans this window gives the member no say over, folded into one slide.
 *
 * A product with a single tier, no decline and no dependant tick list is not a
 * decision — it is a fact about the member's cover, and the coverage tab is
 * where facts about cover live. Given a mount each, those products sat in the
 * enrollment index looking exactly like the ones that needed an answer, and the
 * member had to open every one to find out which was which.
 *
 * The same rule the coverage tab's "What's left" already follows: near-identical
 * tiles that state nothing actionable are collapsed so the ones that do stand
 * out. They are still listed — a member is entitled to see the whole of what
 * they hold on the page that asks them to confirm it — just as rows rather than
 * as nine screenfuls of chrome. */
import { Mount, MountRow } from "@/components/portal/leaf/Mount";

export interface StandardLine {
  code: string;
  /** The product's own name, never its code. */
  name: string;
  /** The tier the member is on, or null when the cohort resolves to none. */
  plan: string | null;
  /** "Your family is covered too" — only where dependant cover is compulsory
   *  AND the member has someone on file, so it never asserts family cover for a
   *  member with no family. */
  familyNote: string | null;
}

export function StandardMount({
  lines,
  rise = true,
}: {
  lines: StandardLine[];
  /** Off inside an enrollment-deck slide, whose own transition owns the
   *  arrival — see `Mount`'s `rise`. */
  rise?: boolean;
}) {
  return (
    <Mount
      as="article"
      rise={rise}
      label="Included as standard"
      gloss="Cover your company sets for you — there's nothing to choose here."
    >
      <dl>
        {lines.map((l) => (
          <MountRow key={l.code} term={l.name} gloss={l.familyNote ?? undefined}>
            {l.plan ?? "Included"}
          </MountRow>
        ))}
      </dl>
    </Mount>
  );
}
