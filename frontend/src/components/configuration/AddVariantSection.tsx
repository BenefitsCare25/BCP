import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { VARIANT_SEP } from "@/lib/insuranceLines";

export interface VariantDraft {
  baseCode: string;
  label: string;
}

export const emptyVariant: VariantDraft = { baseCode: "", label: "" };

/** Client preview of the code the server will assign — mirrors backend
 *  `product_registry.variant_code` (alphanumerics only, max 24). */
export function previewVariantCode(draft: VariantDraft): string {
  const slug = draft.label.toUpperCase().replace(/[^A-Z0-9]+/g, "").slice(0, 24);
  return draft.baseCode && slug ? `${draft.baseCode}${VARIANT_SEP}${slug}` : "";
}

interface Props {
  open: boolean;
  onToggle: () => void;
  draft: VariantDraft;
  onChange: (draft: VariantDraft) => void;
  /** Product types this line offers, as `{code, name}`. */
  types: { code: string; name: string }[];
  /** Codes already in use for this company — a clashing variant is flagged. */
  takenCodes: Set<string>;
}

/**
 * A second, separately placed policy of a product type the company already
 * has — GHS with another insurer, or GHS per legal entity. It gets its own
 * categories, plans, insurer and rates; the type (form, line, claims) comes
 * from the product it is a variant of.
 */
export function AddVariantSection({
  open,
  onToggle,
  draft,
  onChange,
  types,
  takenCodes,
}: Props) {
  const code = previewVariantCode(draft);
  const clash = Boolean(code) && takenCodes.has(code);
  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex items-center gap-2 text-sm font-medium text-foreground"
      >
        <ChevronDown
          className={`size-4 transition-transform ${open ? "" : "-rotate-90"}`}
        />
        Add a variant of a product
        {code && !clash && <Badge variant="good">1</Badge>}
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border p-4">
          <p className="text-sm text-muted-foreground">
            For a second policy of the same product, placed separately (with
            another insurer, or for another legal entity). It keeps its own
            categories, plans and rates.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="av-base">Product</Label>
              <Select
                value={draft.baseCode}
                onValueChange={(v) => onChange({ ...draft, baseCode: v })}
              >
                <SelectTrigger id="av-base">
                  <SelectValue placeholder="Choose a product" />
                </SelectTrigger>
                <SelectContent>
                  {types.map((t) => (
                    <SelectItem key={t.code} value={t.code}>
                      {t.code} · {t.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="av-label">Variant label</Label>
              <Input
                id="av-label"
                placeholder="e.g. AIA, or the entity's short name"
                value={draft.label}
                maxLength={64}
                onChange={(e) => onChange({ ...draft, label: e.target.value })}
              />
            </div>
          </div>
          {code && (
            <p
              className={`text-xs ${clash ? "text-error" : "text-muted-foreground"}`}
              role={clash ? "alert" : undefined}
            >
              {clash ? (
                <>
                  <code className="font-mono">{code}</code> already exists for
                  this company — choose another label.
                </>
              ) : (
                <>
                  Will be added as <code className="font-mono">{code}</code>.
                </>
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
