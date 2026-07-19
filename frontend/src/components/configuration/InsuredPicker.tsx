import { useEntityVocab } from "@/api/hooks";
import { MatchSetPicker } from "@/components/configuration/flex/MatchSetPicker";
import type { VocabValue } from "@/types";

/**
 * The Insured field: which legal entities a category covers.
 *
 * This is a real matching gate — a category naming entities only matches
 * employees whose roster Entity column is one of them — so the value has to
 * agree with the roster spelling. The picker offers roster entities first
 * (with headcounts, so picking one is guaranteed to match), then entities
 * already named elsewhere in the config that match no roster value.
 *
 * Entities are TOKENS, not a comma-joined string: an entity whose registered
 * name contains a comma ("Acme Pte Ltd, Singapore Branch") must stay one
 * entity. Free entry stays available (`allowCustom`) because product setup
 * routinely happens before the roster is uploaded.
 */
export function InsuredPicker({
  policyYearId,
  value,
  onChange,
  label = "Insured entities",
  hint,
}: {
  policyYearId: string | undefined;
  value: string[];
  onChange: (next: string[]) => void;
  label?: string;
  hint?: string;
}) {
  const { data } = useEntityVocab(policyYearId);
  // Roster values first (they carry headcounts and are safe picks), then
  // config-only names so a slip spelling can still be re-selected.
  const options: VocabValue[] = [...(data?.roster ?? []), ...(data?.known ?? [])];

  return (
    <MatchSetPicker
      label={label}
      hint={
        hint ??
        "Which legal entities this category covers. Leave empty to cover every entity. Only employees whose roster Entity matches will match this category."
      }
      selected={value}
      options={options}
      onChange={onChange}
      allowCustom
      placeholder="Search entities…"
      claimedNote=" · already used"
      emptyHint="No roster entities yet — type the entity name and press Enter."
      unknownNote="Not on the current roster — no employee will match this entity"
      countNoun="employee"
    />
  );
}
