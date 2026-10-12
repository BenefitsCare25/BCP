import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { RemoveGuard } from "@/components/configuration/setup/savedEntries";
import type { AttributeSchema } from "@/types";

type Rule = Record<string, unknown> | null;

const isValueMap = (rule: Rule) => rule?.op === "value_map";

/** The API refuses a non-owner's save that shrinks or drops the STORED value
 *  map (schemas_api.update_employee_attribute), so only those removals are
 *  firm-owner actions; mappings added in this edit can be taken out again. */
export function ValueMappingEditor({ rule, savedRule, initialRule, attributes, onChange }: {
  rule: Rule;
  /** The rule stored on the attribute being edited; null when creating one. */
  savedRule: Rule;
  /** The rule the editor opened with, restored when an added value mapping is removed. */
  initialRule: Rule;
  attributes: AttributeSchema[];
  onChange: (rule: Rule) => void;
}) {
  const enabled = isValueMap(rule);
  const rows = enabled ? (rule?.mappings as { from: string; to: string }[]) ?? [] : [];
  const savedCount = isValueMap(savedRule) && Array.isArray(savedRule?.mappings) ? savedRule.mappings.length : 0;
  const sources = [...new Map(attributes.map(a => [a.attribute_id, a])).values()];
  const update = (patch: Record<string, unknown>) => onChange({ ...rule, ...patch });
  return <fieldset className="space-y-3 border-t border-border pt-4">
    <legend className="text-sm font-medium">Company value mappings</legend>
    <p className="text-sm text-muted-foreground">Translate values from an uploaded field into eligibility labels. Original listing values stay available. Re-run matching after saving.</p>
    {!enabled ? <Button type="button" variant="outline" onClick={() => onChange({
      op: "value_map", source: "", unmapped: "omit", mappings: [{ from: "", to: "" }],
    })}>Add value mapping</Button> : <>
      <label className="block space-y-1 text-sm">Source field
        <NativeSelect value={String(rule?.source ?? "")} onChange={e => update({ source: e.target.value })}>
          <option value="">Choose an uploaded field</option>
          {sources.map(a => <option key={a.attribute_id} value={a.attribute_id}>{a.display_name}</option>)}
        </NativeSelect>
      </label>
      {rows.map((row, index) => <div key={index} className="grid grid-cols-2 gap-2">
        <Input aria-label={`Listing value ${index + 1}`} placeholder="Listing value" maxLength={250} value={row.from}
          onChange={e => update({ mappings: rows.map((r, i) => i === index ? { ...r, from: e.target.value } : r) })} />
        <Input aria-label={`Eligibility value ${index + 1}`} placeholder="Eligibility value" maxLength={250} value={row.to}
          onChange={e => update({ mappings: rows.map((r, i) => i === index ? { ...r, to: e.target.value } : r) })} />
        {/* New mappings are appended, so the first `savedCount` rows stand for the stored ones. */}
        <RemoveGuard saved={index < savedCount}><Button type="button" size="sm" variant="ghost" disabled={rows.length <= 1} onClick={() => update({ mappings: rows.filter((_, i) => i !== index) })}>Remove mapping {index + 1}</Button></RemoveGuard>
      </div>)}
      <Button type="button" size="sm" variant="outline" disabled={rows.length >= 500} onClick={() => update({ mappings: [...rows, { from: "", to: "" }] })}>Add another value</Button>
      <label className="block space-y-1 text-sm">Values without a mapping
        <NativeSelect value={String(rule?.unmapped ?? "omit")} onChange={e => update({ unmapped: e.target.value })}>
          <option value="omit">Leave unmapped for review</option>
          <option value="keep">Keep the original value</option>
        </NativeSelect>
      </label>
      <RemoveGuard saved={isValueMap(savedRule)}><Button type="button" size="sm" variant="ghost" className="text-error hover:text-error"
        onClick={() => onChange(isValueMap(initialRule) ? null : initialRule)}>Remove value mapping</Button></RemoveGuard>
    </>}
  </fieldset>;
}
