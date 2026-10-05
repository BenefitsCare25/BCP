import { useId } from "react";
import { Plus, X } from "lucide-react";
import { useEntityVocab, useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { policyMappingIssue, policySourceNumbers } from "@/lib/policyNumbers";
import type { PolicyNumberAssignment } from "@/types";

export function PolicyNumberMappingsEditor({ policyYearId, productCode, source, value, entities, persistedCount, onChange }: {
  policyYearId: string;
  productCode: string;
  source: string;
  value: PolicyNumberAssignment[];
  entities: string[];
  persistedCount: number;
  onChange: (value: PolicyNumberAssignment[]) => void;
}) {
  const id = useId();
  const { data: vocab } = useEntityVocab(policyYearId);
  const { data: me } = useMe();
  const choices = [...new Set([...entities, ...(vocab?.roster ?? []).map((v) => v.value), ...(vocab?.known ?? []).map((v) => v.value)])];
  const issue = policyMappingIssue(source, value);
  const update = (index: number, patch: Partial<PolicyNumberAssignment>) => onChange(value.map((item, i) => i === index ? { ...item, ...patch } : item));
  return (
    <section aria-label={`${productCode} policy-number assignments`} className="flex flex-col gap-3 rounded-md border border-border p-3">
      <div>
        <h3 className="break-words text-sm font-medium">Policy-number assignments · {productCode}</h3>
        <p className="mt-1 text-xs text-muted-foreground">Assign the issued number for this product. Member reports use the employee’s legal entity; dependants use their employee’s entity. Confirm or update setup to apply these assignments.</p>
      </div>
      {policySourceNumbers(source).length > 1 && <p className="text-sm text-warn">The source lists several numbers. Check the policy schedule to select the number for this product and each entity.</p>}
      <datalist id={`${id}-entities`}>{choices.map((entity) => <option key={entity} value={entity} />)}</datalist>
      <datalist id={`${id}-numbers`}>{policySourceNumbers(source).map((number) => <option key={number} value={number} />)}</datalist>
      {value.map((item, index) => (
        <div key={index} className="grid grid-cols-1 items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-scope-${index}`}>Entity scope {index + 1}</Label>
            <Select value={item.entity === null ? "all" : "entity"} onValueChange={(scope) => update(index, { entity: scope === "all" ? null : "" })}>
              <SelectTrigger id={`${id}-scope-${index}`}><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="all">All covered entities</SelectItem><SelectItem value="entity">Specific entity</SelectItem></SelectContent>
            </Select>
            {item.entity !== null && <><Label htmlFor={`${id}-entity-${index}`} className="sr-only">Legal entity {index + 1}</Label><Input id={`${id}-entity-${index}`} list={`${id}-entities`} value={item.entity} maxLength={500} placeholder="Choose or enter the legal entity" onChange={(e) => update(index, { entity: e.target.value })} /></>}
          </div>
          <div className="flex flex-col gap-1.5"><Label htmlFor={`${id}-number-${index}`}>Policy number {index + 1}</Label><Input id={`${id}-number-${index}`} list={`${id}-numbers`} value={item.policy_number} maxLength={64} placeholder="One issued policy number" onChange={(e) => update(index, { policy_number: e.target.value })} /></div>
          {(index >= persistedCount || me?.role === "system_admin") && <Button type="button" variant="ghost" size="icon" aria-label={`Remove policy-number assignment ${index + 1}`} onClick={() => onChange(value.filter((_, i) => i !== index))}><X className="size-4" /></Button>}
        </div>
      ))}
      {!value.length && !issue && <p className="text-sm text-muted-foreground">No issued policy number assigned. Reports will show “Not assigned”.</p>}
      {issue && <p role="status" className="text-sm text-warn">{issue} You can save an incomplete draft.</p>}
      <Button type="button" variant="outline" className="self-start" disabled={value.length >= 100} onClick={() => onChange([...value, { entity: null, policy_number: "" }])}><Plus className="size-4" />Add policy-number assignment</Button>
      {value.some((item) => item.entity === null) && value.some((item) => item.entity !== null) && <p className="text-xs text-muted-foreground">Specific entity assignments take priority. The All covered entities number applies to the remaining covered entities.</p>}
    </section>
  );
}
