import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  JoinerProductRules,
  ListingCategoryOption,
  ListingLabel,
  ListingMapping,
  ListingPreview,
} from "@/api/employeeListing";

/**
 * Review a company's own Employee Listing before it is applied.
 *
 * The listing names each person's category per product in its own wording,
 * which rarely matches the placement slip ("2) All Professionals … - Plan 1
 * (1 Bed Restr)" vs the slip's "All Professionals, Executives & Management
 * Staff", plan 1). Every wording is mapped to a slip category — across all of
 * the product's policies, so a directors' policy shows beside the main one —
 * or marked not covered. Reviewed mappings are remembered for the company.
 */
const NOT_COVERED = "__not_covered__";
const UNSET = "__unset__";

interface Props {
  preview: ListingPreview | null;
  mapping: ListingMapping;
  onLabelChange: (block: number, key: string, value: string) => void;
  onBlockProductToggle: (block: number, code: string, on: boolean) => void;
  dirty: boolean;
  onRecheck: () => void;
  checking: boolean;
  terminateMissing: boolean;
  onTerminateMissingChange: (value: boolean) => void;
  onClose: () => void;
  onApply: () => void;
  applying: boolean;
}

function choiceValue(mapping: ListingMapping, label: ListingLabel): string {
  const choice = mapping.labels[String(label.block)]?.[label.key];
  if (choice?.not_covered) return NOT_COVERED;
  return choice?.category_id ?? UNSET;
}

function optionText(option: ListingCategoryOption): string {
  const plan = option.plan_code ? ` · plan ${option.plan_code}` : "";
  return `${option.product_code} · ${option.category_label}${plan}`;
}

export function EmployeeListingSheet({
  preview,
  mapping,
  onLabelChange,
  onBlockProductToggle,
  dirty,
  onRecheck,
  checking,
  terminateMissing,
  onTerminateMissingChange,
  onClose,
  onApply,
  applying,
}: Props) {
  const members = preview?.members;
  const counts = members?.counts ?? {};
  const missing = members?.missing ?? [];
  const productBlocks = preview?.layout.blocks.filter((b) => b.kind === "product") ?? [];
  const unresolved =
    preview?.labels.filter(
      (l) => (l.employees || l.dependants) && choiceValue(mapping, l) === UNSET,
    ).length ?? 0;
  const blocked = dirty || unresolved > 0;

  return (
    <Sheet open={preview !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="flex w-full flex-col sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Review employee listing</SheetTitle>
        </SheetHeader>
        <SheetBody className="flex-1 space-y-5 overflow-y-auto">
          {preview && (
            <p className="text-sm text-muted-foreground">
              Sheet <span className="text-foreground">{preview.layout.sheet}</span>,
              headers on row {preview.layout.header_row}
              {preview.layout.reference_date &&
                `, ages as at ${preview.layout.reference_date}`}
              .{" "}
              {preview.reused_profile
                ? "Mappings you reviewed before are applied."
                : "Check each suggested mapping."}
            </p>
          )}

          <div className="flex flex-wrap gap-2 text-sm">
            <Badge variant="good">{counts.additions ?? 0} additions</Badge>
            <Badge variant="info">{counts.changes ?? 0} changes</Badge>
            <Badge variant="warn">{counts.deletions ?? 0} terminations</Badge>
            {missing.length > 0 && (
              <Badge variant="default">{missing.length} not in this file</Badge>
            )}
            {(counts.issues ?? 0) > 0 && <Badge variant="error">{counts.issues} issues</Badge>}
          </div>

          {productBlocks.map((block) => {
            const chosen = mapping.block_products[String(block.index)] ?? [];
            // The listing's own numbering ("1) Directors", "2) …") orders the rows.
            const labels = (preview?.labels.filter((l) => l.block === block.index) ?? []).sort(
              (a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }),
            );
            const options = preview?.options[String(block.index)] ?? [];
            return (
              <section key={block.index} className="rounded-lg border border-border">
                <div className="flex flex-wrap items-center justify-between gap-2 bg-muted px-3 py-2">
                  <h3 className="text-sm font-medium text-foreground">{block.banner}</h3>
                  <div className="flex flex-wrap items-center gap-3">
                    {(preview?.products ?? [])
                      .filter(
                        (p) =>
                          chosen.includes(p.code) ||
                          p.code.split("-")[0] === (block.code_hint ?? ""),
                      )
                      .map((p) => (
                        <label
                          key={p.code}
                          className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground"
                          title={p.display_name}
                        >
                          <Checkbox
                            checked={chosen.includes(p.code)}
                            onCheckedChange={(v) =>
                              onBlockProductToggle(block.index, p.code, v === true)
                            }
                          />
                          {p.code}
                        </label>
                      ))}
                  </div>
                </div>
                {labels.length === 0 ? (
                  <p className="px-3 py-2.5 text-sm text-muted-foreground">
                    No category wording in this block.
                  </p>
                ) : (
                  labels.map((label) => (
                    <LabelRow
                      key={label.key}
                      label={label}
                      options={options}
                      value={choiceValue(mapping, label)}
                      onChange={(v) => onLabelChange(label.block, label.key, v)}
                      disabled={dirty}
                    />
                  ))
                )}
              </section>
            );
          })}

          {preview && preview.joiner_rules.length > 0 && (
            <JoinerRules products={preview.joiner_rules} />
          )}

          {preview && preview.checks.length > 0 && (
            <section className="rounded-lg border border-border">
              <div className="bg-muted px-3 py-1.5 text-xs font-medium text-muted-foreground">
                Check these (applied as listed)
              </div>
              {preview.checks.map((check) => (
                <div key={check.code} className="border-t border-border px-3 py-2 text-sm">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
                    <div>
                      <p className="text-foreground">
                        <span className="tabular-nums">{check.count}</span> · {check.message}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Row{check.rows.length === 1 ? "" : "s"} {check.rows.join(", ")}
                        {check.count > check.rows.length ? " …" : ""}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </section>
          )}

          {missing.length > 0 && (
            <section className="space-y-2 rounded-lg border border-border px-3 py-2.5">
              <p className="text-sm text-foreground">
                {missing.length.toLocaleString()} on the roster{" "}
                {missing.length === 1 ? "is" : "are"} not in this listing.
              </p>
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <Checkbox
                  checked={terminateMissing}
                  onCheckedChange={(v) => onTerminateMissingChange(v === true)}
                  className="mt-0.5"
                />
                <span className="text-foreground">
                  Also terminate {missing.length === 1 ? "this person" : "these people"},
                  effective today
                </span>
              </label>
            </section>
          )}

          {members && members.issues.length > 0 && (
            <section className="rounded-lg border border-border">
              <div className="bg-muted px-3 py-1.5 text-xs font-medium text-muted-foreground">
                Issues (skipped)
              </div>
              {members.issues.map((issue, i) => (
                <div key={i} className="border-t border-border px-3 py-2 text-sm text-error">
                  Row {issue.row} ({issue.record_type}): {issue.message}
                </div>
              ))}
            </section>
          )}
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          {dirty && (
            <Button variant="outline" onClick={onRecheck} disabled={checking}>
              {checking ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              Recheck products
            </Button>
          )}
          <Button
            onClick={onApply}
            disabled={applying || blocked}
            title={
              dirty
                ? "Recheck after changing a block's products."
                : unresolved
                  ? `Map ${unresolved} listed categor${unresolved === 1 ? "y" : "ies"} first.`
                  : undefined
            }
          >
            {applying && <Loader2 className="size-4 animate-spin" />}
            Apply listing
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/**
 * How people NOT on this listing (later joiners) will be placed: each
 * product's categories as rules learned from the listing. People on the
 * listing keep their listed category; exceptions are those whose listed
 * category differs from their group's.
 */
function JoinerRules({ products }: { products: JoinerProductRules[] }) {
  return (
    <section className="rounded-lg border border-border">
      <div className="bg-muted px-3 py-1.5 text-xs font-medium text-muted-foreground">
        Rules for joiners not on this listing
      </div>
      {products.map((product) => (
        <div key={product.product_code} className="border-t border-border px-3 py-2">
          <p className="text-sm font-medium text-foreground">
            {product.product_code}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              by {product.attributes.join(" and ")}
              {product.exceptions > 0 &&
                ` · ${product.exceptions} listed exception${product.exceptions === 1 ? "" : "s"} keep their listed cover`}
            </span>
          </p>
          <ul className="mt-1 space-y-0.5">
            {product.rules.map((rule) => (
              <li key={rule.category_id} className="text-xs text-muted-foreground">
                <span className="text-foreground">{rule.category_label}</span>
                {" ← "}
                {rule.rule}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function LabelRow({
  label,
  options,
  value,
  onChange,
  disabled,
}: {
  label: ListingLabel;
  options: ListingCategoryOption[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const people = [
    label.employees ? `${label.employees.toLocaleString()} employees` : null,
    label.dependants ? `${label.dependants.toLocaleString()} dependants` : null,
  ].filter(Boolean);
  const needsCheck = label.source === "suggested" && label.confidence < 0.8;
  return (
    <div className="grid gap-2 border-t border-border px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] sm:items-center">
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground" title={label.label}>
          {label.label}
        </p>
        <p className="text-xs text-muted-foreground">
          {people.join(" · ")}
          {label.plans.length > 0 && ` · plan ${label.plans.join(", ")}`}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Select value={value} onValueChange={onChange} disabled={disabled}>
          <SelectTrigger className="min-w-0 flex-1" aria-label={`Slip category for ${label.label}`}>
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
    </div>
  );
}

export { NOT_COVERED as LISTING_NOT_COVERED };
