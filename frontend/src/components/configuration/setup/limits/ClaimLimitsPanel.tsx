import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDollarSign, Eye, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ClaimLimitScope, ClaimLimitSetting, SobItemAnswer, SobSchedule } from "@/types";
import {
  claimLimitSourceForColumn,
  describeLimit,
  draftDetectedLimitSetting,
  draftLimitSetting,
  looksLikeLimit,
  suggestedScopeCodes,
  type LimitTone,
} from "@/lib/claimLimits";
import { channelFacts, channelSetting, isChannelRow, money } from "@/lib/claimChannels";
import { setColumnProperty, subCellValue } from "@/lib/sob";
import { ChannelEditor, type ChannelDecision } from "./ChannelEditor";
import { ChannelRow, channelName } from "./ChannelRow";
import { hiddenFromMembers } from "@/lib/sobValues";
import { HiddenTag, LimitChip } from "./LimitChip";
import { LimitGrid } from "./LimitGrid";
import { LimitSettingForm } from "./LimitSettingForm";

interface Props {
  sob: SobSchedule;
  claimScopes: ClaimLimitScope[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}

type Selection =
  | { kind: "channel"; uid: string; columnId: string }
  | { kind: "item"; uid: string; columnId: string }
  | { kind: "overall"; columnId: string };

const FOLD_AFTER = 8;

const norm = (value: string | null | undefined) => (value ?? "").trim().replace(/\s+/g, " ").toLowerCase();

/** A non-channel row belongs in the grid once it carries a setting or reads
 * like a limit — on its own cell or on a sub-line (GCSP states its S$3,000 /
 * S$1,500 under "Specialist Care", not on it). */
function isLimitCandidate(item: SobItemAnswer, columnIds: string[]): boolean {
  if (columnIds.some((id) => item.claim_limits?.[id])) return true;
  if (item.sub_items.some((sub) => columnIds.some((id) => looksLikeLimit(subCellValue(sub, id))))) return true;
  return columnIds.some((id) => {
    const source = claimLimitSourceForColumn(item, id);
    if (!source.wording || !looksLikeLimit(source.wording)) return false;
    return source.structuredPolicyYear || draftDetectedLimitSetting(source).basis !== "informational";
  });
}

const readable = (value: string) =>
  /^\d[\d,]*(\.\d+)?$/.test(value.trim()) ? money(value) : value.trim();

/** What a plan's cell says — its wording AND its sub-lines — so "same wording"
 * never copies Plan 2's S$1,500 decision onto a Plan 1 whose sub-line reads
 * "Not covered" just because the row's own cell is blank on both. */
function cellSignature(item: SobItemAnswer, columnId: string): string {
  return [norm(claimLimitSourceForColumn(item, columnId).wording), ...subLines(item, columnId).map(norm)].join("|");
}

/** The row's sub-lines for one plan, as the slip states them. */
function subLines(item: SobItemAnswer, columnId: string): string[] {
  return item.sub_items
    .map((sub) => {
      const value = subCellValue(sub, columnId);
      const name = sub.name.split("(")[0].trim();
      return value?.trim() && name ? `${name}: ${readable(value)}` : null;
    })
    .filter((line): line is string => line !== null);
}

/** A claim type draws from ONE row per plan (backend validation): taking it on
 * one row releases it from the others, which then need another look. */
function releaseScopes(row: SobItemAnswer, columnIds: string[], codes: string[]): SobItemAnswer {
  if (!row.claim_limits || codes.length === 0) return row;
  let touched = false;
  const limits = { ...row.claim_limits };
  for (const id of columnIds) {
    const other = limits[id];
    if (!other) continue;
    const kept = other.claim_scope_codes.filter((code) => !codes.includes(code));
    if (kept.length !== other.claim_scope_codes.length) {
      limits[id] = { ...other, claim_scope_codes: kept, status: "needs_review", source: "manual" };
      touched = true;
    }
  }
  return touched ? { ...row, claim_limits: limits } : row;
}

/**
 * Claim limits for one product, in the two shapes a schedule states them:
 *
 * - CHANNELS — each way an employee is seen (Panel, Polyclinic, Non-Panel,
 *   A&E, teleconsult …) with its per-visit cover, co-pay and yearly cap. Each
 *   is one line of the product, never its overall limit.
 * - PLAN-WIDE and OTHER limits — the overall yearly limit and any other row
 *   that states an amount.
 *
 * A yearly cap is COUNTED (approved claims of its tagged claim type use it up
 * on the employee's "What's left") or SHOWN (the cap is displayed, nothing
 * counts down). "To review" is exactly what Confirm setup refuses.
 */
export function ClaimLimitsPanel({ sob, claimScopes, setSob }: Props) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [extraRows, setExtraRows] = useState<string[]>([]);
  const [addRow, setAddRow] = useState("");
  const columns = sob.columns;
  const columnIds = columns.map((c) => c.id);

  const channels = useMemo(() => sob.items.filter(isChannelRow), [sob.items]);
  const rows = useMemo(
    () =>
      sob.items.filter(
        (item) => !isChannelRow(item) && (isLimitCandidate(item, columnIds) || extraRows.includes(item.uid)),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sob.items, columnIds.join("|"), extraRows],
  );
  const addable = sob.items.filter((item) => !isChannelRow(item) && !rows.includes(item) && item.name.trim());
  // A dental price list states 35 per-procedure amounts; listed in full they
  // bury the one yearly cap that needs a decision. Rows with a setting (or
  // just added) stay; past a handful, the undecided rest fold away.
  const [showAllRows, setShowAllRows] = useState(false);
  const decided = rows.filter((item) => columnIds.some((id) => item.claim_limits?.[id]) || extraRows.includes(item.uid));
  const undecided = rows.filter((item) => !decided.includes(item));
  const folded = !showAllRows && undecided.length > FOLD_AFTER;
  const visibleRows = folded ? decided : rows;

  const cellSetting = (item: SobItemAnswer, columnId: string): ClaimLimitSetting | null =>
    item.claim_limits?.[columnId] ?? null;
  const overallFor = (columnId: string): ClaimLimitSetting | null => {
    const code = columns.find((c) => c.id === columnId)?.plan_codes[0];
    return code ? sob.plan_claim_limits?.[code] ?? null : null;
  };

  const tally = { live: 0, shown: 0, review: 0 };
  const count = (tone: LimitTone) => {
    if (tone === "live") tally.live += 1;
    else if (tone === "review") tally.review += 1;
    else if (tone === "wording") tally.shown += 1;
  };
  // Tally exactly the badges the grids draw: a channel badges only its annual
  // cap, so a channel that merely links a claim type is not a limit.
  for (const item of [...channels, ...rows]) {
    for (const col of columns) {
      const setting = cellSetting(item, col.id);
      if (!setting || (isChannelRow(item) && !channelFacts(item, col.id).yearly)) continue;
      count(describeLimit(setting, claimLimitSourceForColumn(item, col.id), hiddenFromMembers(item)).tone);
    }
  }
  for (const col of columns) {
    const overall = overallFor(col.id);
    if (overall) count(describeLimit(overall, undefined).tone);
  }

  const saveChannel = (uid: string, decision: ChannelDecision) =>
    setSob((current) => {
      const idx = current.items.findIndex((i) => i.uid === uid);
      if (idx < 0) return current;
      let next = current;
      for (const columnId of decision.columnIds) {
        for (const [key, value] of Object.entries(decision.values)) {
          next = setColumnProperty(next, idx, columnId, key, value);
        }
      }
      const edited = next.items[idx];
      const limits = { ...(edited.claim_limits ?? {}) };
      for (const columnId of decision.columnIds) {
        const setting = channelSetting(edited, columnId, decision.counting, decision.scopeCodes);
        if (setting) limits[columnId] = setting;
        else delete limits[columnId];
      }
      return {
        ...next,
        items: next.items.map((row, i) =>
          i === idx ? { ...edited, claim_limits: limits } : releaseScopes(row, decision.columnIds, decision.scopeCodes),
        ),
      };
    });

  const saveItem = (uid: string, columnId: string, next: ClaimLimitSetting, applyToSame: boolean) =>
    setSob((current) => {
      const item = current.items.find((i) => i.uid === uid);
      if (!item) return current;
      const signature = cellSignature(item, columnId);
      const targets = applyToSame
        ? current.columns
            .map((c) => c.id)
            .filter((id) => id === columnId || cellSignature(item, id) === signature)
        : [columnId];
      return {
        ...current,
        items: current.items.map((row) => {
          if (row.uid !== uid) return releaseScopes(row, targets, next.claim_scope_codes);
          const limits = { ...(row.claim_limits ?? {}) };
          for (const id of targets) limits[id] = { ...next };
          return { ...row, claim_limits: limits };
        }),
      };
    });

  const removeItem = (uid: string, columnId: string) =>
    setSob((current) => ({
      ...current,
      items: current.items.map((row) => {
        if (row.uid !== uid || !row.claim_limits?.[columnId]) return row;
        const { [columnId]: _dropped, ...rest } = row.claim_limits;
        return { ...row, claim_limits: rest };
      }),
    }));

  const saveOverall = (columnId: string, next: ClaimLimitSetting, applyToAll: boolean) =>
    setSob((current) => {
      const codes = current.columns.filter((c) => applyToAll || c.id === columnId).flatMap((c) => c.plan_codes);
      const limits = { ...(current.plan_claim_limits ?? {}) };
      for (const code of codes) limits[code] = { ...next, claim_scope_codes: [] };
      return { ...current, plan_claim_limits: limits };
    });

  const removeOverall = (columnId: string) =>
    setSob((current) => {
      const codes = current.columns.find((c) => c.id === columnId)?.plan_codes ?? [];
      const limits = { ...(current.plan_claim_limits ?? {}) };
      for (const code of codes) delete limits[code];
      return { ...current, plan_claim_limits: limits };
    });

  const selectedItem = selection && selection.kind !== "overall" ? sob.items.find((i) => i.uid === selection.uid) ?? null : null;
  const selectedColumn = columns.find((c) => c.id === selection?.columnId) ?? null;
  const close = () => setSelection(null);

  if (columns.length === 0) return null;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="claim-limits-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="claim-limits-heading" className="flex items-center gap-2 text-base font-semibold text-foreground">
          <CircleDollarSign className="size-4 text-primary" aria-hidden /> Claim limits
        </h3>
        <div className="flex flex-wrap gap-2 text-xs" aria-live="polite">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-good/40 bg-good/10 px-2.5 py-1 text-foreground">
            <CheckCircle2 className="size-3.5 text-good" aria-hidden /> {tally.live} on What's left
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-foreground">
            <Eye className="size-3.5 text-muted-foreground" aria-hidden /> {tally.shown} no drawdown
          </span>
          {tally.review > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-warn/50 bg-warn/10 px-2.5 py-1 text-foreground">
              <AlertTriangle className="size-3.5 text-warn" aria-hidden /> {tally.review} to review
            </span>
          )}
        </div>
      </div>

      {channels.length > 0 && (
        <div className="space-y-2">
          <LimitGrid
            id="claim-limit-channels"
            title="Clinic channels"
            hideTitle
            rowHeader="Channel"
            columns={columns}
          >
            {channels.map((item) => (
              <ChannelRow
                key={item.uid}
                item={item}
                columns={columns}
                rows={channels}
                claimScopes={claimScopes}
                activeColumnId={selection?.kind === "channel" && selection.uid === item.uid ? selection.columnId : null}
                onSelect={(columnId) => setSelection({ kind: "channel", uid: item.uid, columnId })}
              />
            ))}
          </LimitGrid>
          {selection?.kind === "channel" && selectedItem && selectedColumn && (
            <div>
              <ChannelEditor
                key={`${selectedItem.uid}-${selectedColumn.id}`}
                title={`${channelName(selectedItem)} · ${selectedColumn.label}`}
                item={selectedItem}
                column={selectedColumn}
                columns={columns}
                claimScopes={claimScopes}
                suggested={suggestedScopeCodes(claimScopes, selectedItem.name)}
                onSave={(decision) => {
                  saveChannel(selectedItem.uid, decision);
                  close();
                }}
                onCancel={close}
              />
            </div>
          )}
        </div>
      )}

      <div className="space-y-2">
        <LimitGrid
          id="claim-limit-other"
          title="Benefit limits"
          rowHeader="Benefit"
          columns={columns}
        >
          <tr className="border-b border-border last:border-0">
            <th scope="row" className="bg-card px-3 py-2 text-left font-normal sm:sticky sm:left-0 sm:z-10">
              <span className="block text-sm font-medium text-foreground">Overall annual limit</span>
            </th>
            {columns.map((col) => {
              const setting = overallFor(col.id);
              const { text, tone } = setting ? describeLimit(setting, undefined) : { text: "None", tone: "none" as LimitTone };
              return (
                <td key={col.id} className="px-2 py-2">
                  <LimitChip
                    text={text}
                    tone={tone}
                    active={selection?.kind === "overall" && selection.columnId === col.id}
                    onClick={() => setSelection({ kind: "overall", columnId: col.id })}
                  />
                </td>
              );
            })}
          </tr>
          {visibleRows.map((item) => (
            <tr key={item.uid} className="border-b border-border last:border-0">
              <th scope="row" className="bg-card px-3 py-2 text-left font-normal sm:sticky sm:left-0 sm:z-10">
                <span className="line-clamp-2 break-words text-sm text-foreground" title={item.name}>
                  {item.name || `Line ${item.number}`}
                </span>
                {hiddenFromMembers(item) && <HiddenTag />}
              </th>
              {columns.map((col) => {
                const setting = cellSetting(item, col.id);
                const source = claimLimitSourceForColumn(item, col.id);
                const described = describeLimit(setting, source, hiddenFromMembers(item));
                const lines = !setting && !source.wording ? subLines(item, col.id) : [];
                const text = lines.length ? lines.join(" · ") : described.text;
                const tone: LimitTone = lines.length ? "wording" : described.tone;
                const scopes = (setting?.claim_scope_codes ?? [])
                  .map((code) => claimScopes.find((s) => s.code === code)?.label ?? code)
                  .join(" · ");
                return (
                  <td key={col.id} className="px-2 py-2 align-top">
                    <LimitChip
                      text={text}
                      tone={setting ? tone : source.wording || lines.length ? "wording" : "none"}
                      sub={scopes}
                      unset={!setting}
                      active={selection?.kind === "item" && selection.uid === item.uid && selection.columnId === col.id}
                      onClick={() => setSelection({ kind: "item", uid: item.uid, columnId: col.id })}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </LimitGrid>
        {undecided.length > FOLD_AFTER && (
          <Button type="button" size="sm" variant="ghost" className="self-start" onClick={() => setShowAllRows((v) => !v)}>
            {folded ? `Show ${undecided.length} more benefits` : "Show fewer"}
          </Button>
        )}

        {selection && selection.kind !== "channel" && selectedColumn && (
          <div>
            {selection.kind === "overall" ? (
              <LimitSettingForm
                key={`overall-${selection.columnId}`}
                title={`Overall annual limit · ${selectedColumn.label}`}
                idPrefix={`overall-${selection.columnId}`}
                overall
                setting={overallFor(selection.columnId) ?? { ...draftLimitSetting(null), basis: "policy_year" }}
                wording={undefined}
                scopes={[]}
                suggested={[]}
                sameWordingCount={columns.length - 1}
                onSave={(next, all) => {
                  saveOverall(selection.columnId, next, all);
                  close();
                }}
                onRemove={overallFor(selection.columnId) ? () => { removeOverall(selection.columnId); close(); } : undefined}
                onCancel={close}
              />
            ) : selectedItem ? (
              <ItemForm
                title={`${selectedItem.name || `Line ${selectedItem.number}`} · ${selectedColumn.label}`}
                item={selectedItem}
                columnId={selection.columnId}
                columnIds={columnIds}
                claimScopes={claimScopes}
                setting={cellSetting(selectedItem, selection.columnId)}
                onSave={(next, same) => {
                  saveItem(selectedItem.uid, selection.columnId, next, same);
                  close();
                }}
                onRemove={() => {
                  removeItem(selectedItem.uid, selection.columnId);
                  close();
                }}
                onCancel={close}
              />
            ) : null}
          </div>
        )}
      </div>

      {addable.length > 0 && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1">
            <label className="text-xs font-medium text-foreground" htmlFor="claim-limit-add-row">
              Add benefit limit
            </label>
            <Select value={addRow} onValueChange={setAddRow}>
              <SelectTrigger id="claim-limit-add-row">
                <SelectValue placeholder="Select benefit" />
              </SelectTrigger>
              <SelectContent>
                {addable.map((item) => (
                  <SelectItem key={item.uid} value={item.uid}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!addRow}
            onClick={() => {
              setExtraRows((current) => [...current, addRow]);
              setSelection({ kind: "item", uid: addRow, columnId: columns[0].id });
              setAddRow("");
            }}
          >
            <Plus className="size-3.5" aria-hidden /> Add
          </Button>
        </div>
      )}
    </section>
  );
}

function ItemForm({
  title,
  item,
  columnId,
  columnIds,
  claimScopes,
  setting,
  onSave,
  onRemove,
  onCancel,
}: {
  title: string;
  item: SobItemAnswer;
  columnId: string;
  columnIds: string[];
  claimScopes: ClaimLimitScope[];
  setting: ClaimLimitSetting | null;
  onSave: (next: ClaimLimitSetting, applyToSame: boolean) => void;
  onRemove: () => void;
  onCancel: () => void;
}) {
  const source = claimLimitSourceForColumn(item, columnId);
  const signature = cellSignature(item, columnId);
  const sameWordingCount = columnIds.filter(
    (id) => id !== columnId && cellSignature(item, id) === signature,
  ).length;
  const suggested = suggestedScopeCodes(claimScopes, item.name);
  return (
    <LimitSettingForm
      key={`${item.uid}-${columnId}`}
      title={title}
      idPrefix={`limit-${item.uid}-${columnId}`}
      setting={setting ?? draftDetectedLimitSetting(source, suggested)}
      fresh={!setting}
      hidden={hiddenFromMembers(item)}
      wording={source.wording}
      context={subLines(item, columnId)}
      scopes={claimScopes}
      suggested={suggested}
      sameWordingCount={sameWordingCount}
      onSave={onSave}
      onRemove={setting ? onRemove : undefined}
      onCancel={onCancel}
    />
  );
}
