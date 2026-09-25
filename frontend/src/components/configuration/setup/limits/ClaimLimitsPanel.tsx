import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDollarSign, Plus } from "lucide-react";
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
import { LimitChip } from "./LimitChip";
import { LimitSettingForm } from "./LimitSettingForm";

interface Props {
  sob: SobSchedule;
  claimScopes: ClaimLimitScope[];
  setSob: (fn: (s: SobSchedule) => SobSchedule) => void;
}

type Selection = { kind: "item"; uid: string; columnId: string } | { kind: "overall"; columnId: string };

const norm = (value: string | null | undefined) => (value ?? "").trim().replace(/\s+/g, " ").toLowerCase();

/** A row belongs in the grid once it carries a setting or reads like a limit. */
function isLimitCandidate(item: SobItemAnswer, columnIds: string[]): boolean {
  if (columnIds.some((id) => item.claim_limits?.[id])) return true;
  return columnIds.some((id) => {
    const source = claimLimitSourceForColumn(item, id);
    if (!source.wording || !looksLikeLimit(source.wording)) return false;
    return source.structuredPolicyYear || draftDetectedLimitSetting(source).basis !== "informational";
  });
}

/**
 * Claim limits for one product: every limit-bearing benefit row × every
 * benefit column, each cell one plain-English chip. The broker reviews the
 * whole product in one view — GHS's 13 plans no longer need 13 passes through
 * a plan dropdown — and edits a cell below the grid.
 *
 * Only a TRACKED limit (yearly S$ or visits per year) counts down on the
 * member's "What's left" and guards approval; everything else is a condition
 * shown as wording. Nothing here is live until the setup is confirmed.
 */
export function ClaimLimitsPanel({ sob, claimScopes, setSob }: Props) {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [extraRows, setExtraRows] = useState<string[]>([]);
  const [addRow, setAddRow] = useState("");
  const columns = sob.columns;
  const columnIds = columns.map((c) => c.id);

  const rows = useMemo(
    () => sob.items.filter((item) => isLimitCandidate(item, columnIds) || extraRows.includes(item.uid)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sob.items, columnIds.join("|"), extraRows],
  );
  const addable = sob.items.filter((item) => !rows.includes(item) && item.name.trim());

  const cellSetting = (item: SobItemAnswer, columnId: string): ClaimLimitSetting | null =>
    item.claim_limits?.[columnId] ?? null;
  const overallFor = (columnId: string): ClaimLimitSetting | null => {
    const code = columns.find((c) => c.id === columnId)?.plan_codes[0];
    return code ? sob.plan_claim_limits?.[code] ?? null : null;
  };

  const tally = { live: 0, review: 0 };
  for (const item of rows) {
    for (const col of columns) {
      const { tone } = describeLimit(cellSetting(item, col.id), claimLimitSourceForColumn(item, col.id).wording);
      if (tone === "live") tally.live += 1;
      if (tone === "review") tally.review += 1;
    }
  }
  for (const col of columns) {
    const { tone } = describeLimit(overallFor(col.id), undefined);
    if (overallFor(col.id) && tone === "live") tally.live += 1;
    if (overallFor(col.id) && tone === "review") tally.review += 1;
  }

  const saveItem = (uid: string, columnId: string, next: ClaimLimitSetting, applyToSame: boolean) =>
    setSob((current) => {
      const item = current.items.find((i) => i.uid === uid);
      if (!item) return current;
      const wording = norm(claimLimitSourceForColumn(item, columnId).wording);
      const targets = applyToSame
        ? current.columns
            .map((c) => c.id)
            .filter((id) => id === columnId || norm(claimLimitSourceForColumn(item, id).wording) === wording)
        : [columnId];
      return {
        ...current,
        items: current.items.map((row) => {
          if (row.uid === uid) {
            const limits = { ...(row.claim_limits ?? {}) };
            for (const id of targets) limits[id] = { ...next };
            return { ...row, claim_limits: limits };
          }
          // A claim type draws from ONE row per plan (backend validation);
          // taking it here releases it elsewhere, which then needs a look.
          let touched = false;
          const limits = { ...(row.claim_limits ?? {}) };
          for (const id of targets) {
            const other = limits[id];
            if (!other) continue;
            const kept = other.claim_scope_codes.filter((code) => !next.claim_scope_codes.includes(code));
            if (kept.length !== other.claim_scope_codes.length) {
              limits[id] = { ...other, claim_scope_codes: kept, status: "needs_review", source: "manual" };
              touched = true;
            }
          }
          return touched ? { ...row, claim_limits: limits } : row;
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
      const codes = current.columns
        .filter((c) => applyToAll || c.id === columnId)
        .flatMap((c) => c.plan_codes);
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

  const selectedItem =
    selection?.kind === "item" ? sob.items.find((i) => i.uid === selection.uid) ?? null : null;
  const selectedColumn = columns.find((c) => c.id === selection?.columnId) ?? null;

  if (columns.length === 0) return null;

  return (
    <section className="flex flex-col gap-3" aria-labelledby="claim-limits-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <h3 id="claim-limits-heading" className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <CircleDollarSign className="size-4 text-primary" aria-hidden /> Claim limits
          </h3>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">Tracked</span> limits (a yearly amount or a number of
            visits) count down on the employee's “What's left” and guard claim approval. Everything else shows to
            employees and assessors as a condition. Click a cell to review it.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          <span className="inline-flex items-center gap-1.5 text-good">
            <CheckCircle2 className="size-3.5" aria-hidden /> {tally.live} tracked
          </span>
          {tally.review > 0 && (
            <span className="inline-flex items-center gap-1.5 text-warn">
              <AlertTriangle className="size-3.5" aria-hidden /> {tally.review} to review
            </span>
          )}
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-muted">
            <tr className="border-b border-border">
              <th className="min-w-40 bg-muted sm:sticky sm:left-0 sm:z-10 sm:min-w-56 px-3 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground">
                Benefit
              </th>
              {columns.map((col) => (
                <th key={col.id} className="min-w-44 px-2 py-1.5 text-left text-2xs uppercase tracking-wider text-muted-foreground">
                  <span className="block truncate" title={col.label}>{col.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-border">
              <td className="bg-card px-3 py-2 sm:sticky sm:left-0 sm:z-10">
                <span className="block text-sm font-medium text-foreground">Overall yearly limit</span>
                <span className="block text-2xs text-muted-foreground">Across every claim on the plan</span>
              </td>
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
            {rows.map((item) => (
              <tr key={item.uid} className="border-b border-border last:border-0">
                <td className="bg-card px-3 py-2 sm:sticky sm:left-0 sm:z-10">
                  <span className="block max-w-72 truncate text-sm text-foreground" title={item.name}>
                    {item.name || `Line ${item.number}`}
                  </span>
                </td>
                {columns.map((col) => {
                  const setting = cellSetting(item, col.id);
                  const wording = claimLimitSourceForColumn(item, col.id).wording;
                  const { text, tone } = describeLimit(setting, wording);
                  const scopes = (setting?.claim_scope_codes ?? [])
                    .map((code) => claimScopes.find((s) => s.code === code)?.label ?? code)
                    .join(" · ");
                  return (
                    <td key={col.id} className="px-2 py-2 align-top">
                      <LimitChip
                        text={text}
                        tone={setting ? tone : wording ? "wording" : "none"}
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
          </tbody>
        </table>
      </div>

      {selection && selectedColumn && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-foreground">
            {selectedColumn.label} · {selection.kind === "overall" ? "Overall yearly limit" : selectedItem?.name}
          </p>
          {selection.kind === "overall" ? (
            <LimitSettingForm
              key={`overall-${selection.columnId}`}
              idPrefix={`overall-${selection.columnId}`}
              overall
              setting={overallFor(selection.columnId) ?? { ...draftLimitSetting(null), basis: "policy_year" }}
              wording={undefined}
              scopes={[]}
              suggested={[]}
              sameWordingCount={columns.length - 1}
              onSave={(next, all) => {
                saveOverall(selection.columnId, next, all);
                setSelection(null);
              }}
              onRemove={overallFor(selection.columnId) ? () => { removeOverall(selection.columnId); setSelection(null); } : undefined}
              onCancel={() => setSelection(null)}
            />
          ) : selectedItem ? (
            <ItemForm
              item={selectedItem}
              columnId={selection.columnId}
              columnIds={columnIds}
              claimScopes={claimScopes}
              setting={cellSetting(selectedItem, selection.columnId)}
              onSave={(next, same) => {
                saveItem(selectedItem.uid, selection.columnId, next, same);
                setSelection(null);
              }}
              onRemove={() => {
                removeItem(selectedItem.uid, selection.columnId);
                setSelection(null);
              }}
              onCancel={() => setSelection(null)}
            />
          ) : null}
        </div>
      )}

      {addable.length > 0 && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1">
            <label className="text-xs font-medium text-foreground" htmlFor="claim-limit-add-row">
              Add a limit to another benefit
            </label>
            <Select value={addRow} onValueChange={setAddRow}>
              <SelectTrigger id="claim-limit-add-row">
                <SelectValue placeholder="Choose a benefit line" />
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
  item,
  columnId,
  columnIds,
  claimScopes,
  setting,
  onSave,
  onRemove,
  onCancel,
}: {
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
  const wording = norm(source.wording);
  const sameWordingCount = columnIds.filter(
    (id) => id !== columnId && norm(claimLimitSourceForColumn(item, id).wording) === wording,
  ).length;
  const suggested = suggestedScopeCodes(claimScopes, item.name);
  return (
    <LimitSettingForm
      key={`${item.uid}-${columnId}`}
      idPrefix={`limit-${item.uid}-${columnId}`}
      setting={setting ?? draftDetectedLimitSetting(source, suggested)}
      wording={source.wording}
      scopes={claimScopes}
      suggested={suggested}
      sameWordingCount={sameWordingCount}
      onSave={onSave}
      onRemove={setting ? onRemove : undefined}
      onCancel={onCancel}
    />
  );
}
