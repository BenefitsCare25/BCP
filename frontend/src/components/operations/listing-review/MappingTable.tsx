import { ArrowRight } from "lucide-react";
import type {
  ListingBlock,
  ListingCategoryOption,
  ListingLabel,
  ListingMapping,
  ListingPreview,
} from "@/api/employeeListing";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { SectionLabel } from "@/components/ui/section-label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { count } from "./format";

/**
 * Every product block of the listing, each category wording it uses (left,
 * from the employee listing) beside the placement slip category it maps to
 * (right). One table rather than a card per product keeps seven products on
 * one screen; the product's own banner heads its rows.
 *
 * Each row states once who its choice covers, live: the file's totals live in
 * the summary above, so a wording covering the whole file reads "All
 * employees and dependants" rather than repeating them on every product.
 */
export const NOT_COVERED = "__not_covered__";
export const UNSET = "__unset__";
// Listing wording → arrow → slip category, shared by the heading and rows.
const GRID = "grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)]";
const NBSP = String.fromCharCode(0xa0);

export function choiceValue(mapping: ListingMapping, label: ListingLabel): string {
  const choice = mapping.labels[String(label.block)]?.[label.key];
  if (choice?.not_covered) return NOT_COVERED;
  return choice?.category_id ?? UNSET;
}

/** Wordings that cover someone but have no decision yet. */
export function unmappedCount(preview: ListingPreview, mapping: ListingMapping): number {
  return preview.labels.filter(
    (l) => (l.employees || l.dependants) && choiceValue(mapping, l) === UNSET,
  ).length;
}

function optionText(option: ListingCategoryOption): string {
  // Non-breaking: "plan" and its code never wrap apart in a narrow trigger.
  const plan = option.plan_code ? ` · plan${NBSP}${option.plan_code}` : "";
  return `${option.product_code} · ${option.category_label}${plan}`;
}

/**
 * The slip products a block may report. A block naming a product offers that
 * product's policies and what the preview suggested for it; one naming none
 * offers every product no other block has taken, so one product is never read
 * from two blocks and ticking one never hides the others.
 */
function offeredProducts(
  preview: ListingPreview,
  mapping: ListingMapping,
  block: ListingBlock,
): ListingPreview["products"] {
  const key = String(block.index);
  const chosen = new Set(mapping.block_products[key] ?? []);
  if (block.code_hint) {
    const suggested = new Set(preview.block_products[key] ?? []);
    const hinted = preview.products.filter(
      (p) =>
        chosen.has(p.code) || suggested.has(p.code) || p.code.split("-")[0] === block.code_hint,
    );
    if (hinted.length > 0) return hinted;
  }
  const taken = new Set(
    Object.entries(mapping.block_products)
      .filter(([other]) => other !== key)
      .flatMap(([, codes]) => codes),
  );
  return preview.products.filter((p) => chosen.has(p.code) || !taken.has(p.code));
}

/** Employees and dependants a wording puts under its product. */
function people(label: ListingLabel): { employees: number; dependants: number } {
  return { employees: label.employees, dependants: label.dependants + label.tier_dependants };
}

function headcount(n: { employees: number; dependants: number }): string {
  return `${count(n.employees, "employee")} · ${count(n.dependants, "dependant")}`;
}

/**
 * Who is covered under a product with the current choices, counted from the
 * listing's rows and recomputed on every change: a wording mapped to "Not
 * covered", or not mapped yet, adds nobody.
 */
function coveredUnder(labels: ListingLabel[], mapping: ListingMapping) {
  const total = { employees: 0, dependants: 0, tierOnly: false };
  for (const label of labels) {
    const value = choiceValue(mapping, label);
    if (value === UNSET || value === NOT_COVERED) continue;
    const n = people(label);
    total.employees += n.employees;
    total.dependants += n.dependants;
    total.tierOnly ||= label.tier_dependants > 0;
  }
  return total;
}

interface Props {
  preview: ListingPreview;
  mapping: ListingMapping;
  onLabelChange: (block: number, key: string, value: string) => void;
  onBlockProductToggle: (block: number, code: string, on: boolean) => void;
  disabled: boolean;
}

export function MappingTable({
  preview,
  mapping,
  onLabelChange,
  onBlockProductToggle,
  disabled,
}: Props) {
  const blocks = preview.layout.blocks.filter((b) => b.kind === "product");
  const total = preview.labels.filter((l) => l.employees || l.dependants).length;
  const unmapped = unmappedCount(preview, mapping);
  return (
    <section aria-labelledby="listing-categories" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="listing-categories" className="text-sm font-semibold text-foreground">
          Categories
        </h3>
        <span className="text-xs text-muted-foreground">
          {unmapped > 0 ? (
            <span className="font-medium text-error">
              {count(unmapped, "category", "categories")} to map
            </span>
          ) : (
            `All ${count(total, "category", "categories")} mapped`
          )}
        </span>
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        <div aria-hidden className={`grid gap-2 px-3 py-1.5 ${GRID}`}>
          <SectionLabel as="span">Employee listing</SectionLabel>
          <ArrowRight className="size-3.5 self-center text-muted-foreground" />
          <SectionLabel as="span">Placement slip category</SectionLabel>
        </div>
        {blocks.map((block) => (
          <ProductGroup
            key={block.index}
            block={block}
            preview={preview}
            mapping={mapping}
            onLabelChange={onLabelChange}
            onBlockProductToggle={onBlockProductToggle}
            disabled={disabled}
          />
        ))}
      </div>
    </section>
  );
}

function ProductGroup({
  block,
  preview,
  mapping,
  onLabelChange,
  onBlockProductToggle,
  disabled,
}: Props & { block: ListingBlock }) {
  const chosen = mapping.block_products[String(block.index)] ?? [];
  // The listing's own numbering ("1) Directors", "2) …") orders the rows.
  const labels = preview.labels
    .filter((l) => l.block === block.index)
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  const options = preview.options[String(block.index)] ?? [];
  const column = block.columns.find((c) => c.role === "category")?.letter;
  const products = offeredProducts(preview, mapping, block);
  const covered = coveredUnder(labels, mapping);
  return (
    <div role="group" aria-label={block.banner} className="border-t border-border">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 bg-muted px-3 py-1.5">
        <div className="min-w-0">
          <p className="text-xs font-medium text-foreground">
            {block.banner}
            {column && (
              <span className="font-normal text-muted-foreground"> · column {column}</span>
            )}
          </p>
          <p aria-live="polite" className="text-xs tabular-nums text-foreground">
            <span className="text-muted-foreground">Covered: </span>
            {headcount(covered)}
            {covered.tierOnly && covered.dependants > 0 && (
              <span className="text-muted-foreground"> (dependants by family tier)</span>
            )}
          </p>
        </div>
        <div
          role="group"
          aria-label={`Placement slip products for ${block.banner}`}
          className="flex flex-wrap items-center gap-x-3 gap-y-1"
        >
          <span className="text-xs text-muted-foreground">Slip product</span>
          {products.map((p) => (
            <label
              key={p.code}
              className="flex cursor-pointer items-center gap-1.5 text-xs text-foreground"
              title={p.display_name}
            >
              <Checkbox
                checked={chosen.includes(p.code)}
                onCheckedChange={(v) => onBlockProductToggle(block.index, p.code, v === true)}
                aria-label={`${p.code} — ${p.display_name}`}
              />
              {p.code}
            </label>
          ))}
        </div>
      </div>
      {labels.length === 0 ? (
        <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
          No category wording in this block.
        </p>
      ) : (
        labels.map((label) => (
          <LabelRow
            key={label.key}
            label={label}
            options={options}
            value={choiceValue(mapping, label)}
            // A product with one wording has its count in the header already.
            cover={labels.length > 1 ? headcount(people(label)) : null}
            onChange={(v) => onLabelChange(label.block, label.key, v)}
            disabled={disabled}
          />
        ))
      )}
    </div>
  );
}

function LabelRow({
  label,
  options,
  value,
  cover,
  onChange,
  disabled,
}: {
  label: ListingLabel;
  options: ListingCategoryOption[];
  value: string;
  cover: string | null;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const needsCheck = label.source === "suggested" && label.confidence < 0.8;
  return (
    <div className="grid gap-x-2 gap-y-1.5 border-t border-border px-3 py-2 sm:grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)] sm:items-center">
      <p className="line-clamp-2 min-w-0 break-words text-sm text-foreground" title={label.label}>
        {label.label}
      </p>
      <ArrowRight aria-hidden className="hidden size-4 text-muted-foreground sm:block" />
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Select value={value} onValueChange={onChange} disabled={disabled}>
            <SelectTrigger
              className="h-auto min-h-9 min-w-0 flex-1 py-1.5 text-left [&>span]:line-clamp-2"
              aria-label={`Placement slip category for listing category ${label.label}`}
            >
              <SelectValue placeholder="Choose a slip category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNSET} disabled>
                Choose a slip category
              </SelectItem>
              {options.map((o) => (
                <SelectItem key={o.category_id} value={o.category_id}>
                  {optionText(o)}
                </SelectItem>
              ))}
              <SelectItem value={NOT_COVERED}>Not covered</SelectItem>
            </SelectContent>
          </Select>
          {value === UNSET ? (
            <Badge variant="error">Not mapped</Badge>
          ) : needsCheck ? (
            <Badge variant="warn">Check</Badge>
          ) : null}
        </div>
        {cover && (
          <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
            {cover}
          </p>
        )}
      </div>
    </div>
  );
}
