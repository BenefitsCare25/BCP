import { cn } from "@/lib/cn";
import type { ClaimLimitScope, SobColumn, SobItemAnswer } from "@/types";
import { claimLimitSourceForColumn, describeLimit, type LimitTone } from "@/lib/claimLimits";
import { channelFacts, duplicateOf, yearlyText } from "@/lib/claimChannels";
import { hiddenFromMembers } from "@/lib/sobValues";
import { HiddenTag, TONE_LABEL } from "./LimitChip";

const TONE_CLASS: Record<LimitTone, string> = {
  live: "border-good/40 bg-good/10",
  review: "border-warn/50 bg-warn/10",
  wording: "border-border bg-card",
  none: "border-dashed border-border",
};


/** "Panel — per visit / co-payment / per policy year" → "Panel". */
export const channelName = (item: SobItemAnswer) =>
  item.name.split(/\s[—–]\s/)[0].trim() || `Line ${item.number}`;

export function ChannelRow({
  item,
  columns,
  rows,
  claimScopes,
  activeColumnId,
  onSelect,
}: {
  item: SobItemAnswer;
  columns: SobColumn[];
  rows: SobItemAnswer[];
  claimScopes: ClaimLimitScope[];
  activeColumnId: string | null;
  onSelect: (columnId: string) => void;
}) {
  const facts = columns.map((col) => channelFacts(item, col.id));
  const hidden = hiddenFromMembers(item);
  const allEmpty = facts.every((f) => f.empty);
  const twin = allEmpty ? duplicateOf(item, rows) : null;
  const tagged = [
    ...new Set(columns.flatMap((col) => item.claim_limits?.[col.id]?.claim_scope_codes ?? [])),
  ].map((code) => claimScopes.find((s) => s.code === code)?.label ?? code);

  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="bg-card px-3 py-2 text-left align-top font-normal sm:sticky sm:left-0 sm:z-10">
        <span className="line-clamp-2 break-words text-sm font-medium text-foreground" title={item.name}>
          {channelName(item)}
        </span>
        {hidden && <HiddenTag />}
        <span className="block text-2xs leading-4 text-muted-foreground">
          {allEmpty
            ? twin
              ? `Duplicate of ${channelName(twin)}`
              : "Empty"
            : tagged.length
              ? tagged.join(" · ")
              : "No claim type"}
        </span>
      </th>
      {columns.map((col, index) => {
        const f = facts[index];
        const setting = item.claim_limits?.[col.id] ?? null;
        const { tone } = describeLimit(setting, claimLimitSourceForColumn(item, col.id), hidden);
        const yearlyTone: LimitTone = f.yearly ? (setting ? tone : f.yearly.unit === null ? "review" : "wording") : "none";
        const active = activeColumnId === col.id;
        const muted = f.notCovered || f.empty;
        return (
          <td key={col.id} className="px-2 py-2 align-top">
            <button
              type="button"
              aria-pressed={active}
              aria-label={`${channelName(item)}, ${col.label}: edit`}
              onClick={() => onSelect(col.id)}
              className={cn(
                "w-full rounded-md border px-2 py-1.5 text-left text-xs transition-colors hover:border-foreground/30",
                muted ? TONE_CLASS.none : f.yearly ? TONE_CLASS[yearlyTone] : TONE_CLASS.wording,
                active && "ring-2 ring-ring/40",
              )}
            >
              <span className={cn("block", muted ? "text-muted-foreground" : "text-foreground")}>
                {f.empty ? "—" : f.notCovered ? "Not covered" : f.cover ?? "—"}
              </span>
              {!muted && f.copay && <span className="block text-2xs text-muted-foreground">{f.copay}</span>}
              {f.yearly && (
                <span className="mt-1 flex items-center gap-1.5 text-2xs">
                  <span className="shrink-0 font-medium uppercase tracking-wider text-muted-foreground">
                    {setting ? TONE_LABEL[yearlyTone] : "Not set"}
                  </span>
                  <span className="min-w-0 truncate text-foreground" title={yearlyText(f.yearly)}>
                    {yearlyText(f.yearly)}
                  </span>
                </span>
              )}
            </button>
          </td>
        );
      })}
    </tr>
  );
}
