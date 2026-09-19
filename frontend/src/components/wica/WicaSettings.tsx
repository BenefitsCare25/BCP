import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useMe } from "@/api/hooks";
import { wicaApi, type WicaSettings as Settings } from "@/api/wica";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Failure, Field, Loading } from "./shared";

export function WicaSettings() {
  const clientId = useSession(s => s.activeClientId);
  return clientId ? <SettingsLoader key={clientId} clientId={clientId} /> : <p className="text-sm text-muted-foreground">Select a company.</p>;
}
function SettingsLoader({ clientId }: { clientId: string }) {
  const query = useQuery({ queryKey: ["wica", clientId, "settings"], queryFn: () => wicaApi(clientId).get<Settings>("/settings") });
  if (query.isPending) return <Loading />;
  if (query.isError) return <Failure error={query.error} retry={() => void query.refetch()} />;
  return <SettingsForm key={query.data.revision} clientId={clientId} initial={query.data} />;
}
function SettingsForm({ clientId, initial }: { clientId: string; initial: Settings }) {
  const { data: me } = useMe();
  const editable = me?.role === "broker_admin" || me?.role === "system_admin";
  const [value, setValue] = useState(initial);
  const [saved, setSaved] = useState(false);
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => wicaApi(clientId).put<Settings>("/settings", { ...value, periods: value.periods.map(({ in_use: _used, ...period }) => period) }),
    onSuccess: result => { setValue(result); setSaved(true); void qc.invalidateQueries({ queryKey: ["wica", clientId] }); },
  });
  return <form className="space-y-5" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
    <fieldset disabled={!editable || mutation.isPending} className="space-y-5">
      <label className="flex w-fit items-center gap-3 text-sm font-medium"><input type="checkbox" checked={value.enabled} onChange={e => { setSaved(false); setValue({ ...value, enabled: e.target.checked }); }} className="size-4 accent-primary" />Enable WICA</label>
      <div className="divide-y divide-border rounded-xl border border-border bg-card px-4">
        {value.periods.map((period, i) => <div key={period.id} className="grid gap-4 py-4 md:grid-cols-[2fr_1fr_1fr_1fr]">
          {([['label', 'Period label', 'text'], ['start_date', 'Start date', 'date'], ['end_date', 'End date', 'date'], ['grace_days', 'Grace period (days, optional)', 'number']] as const).map(([key, label, type]) => <Field key={key} label={label}><Input type={type} required={key !== "grace_days"} min={key === "grace_days" ? 0 : undefined} max={key === "grace_days" ? 3650 : undefined} maxLength={key === "label" ? 100 : undefined} disabled={period.in_use} value={period[key] ?? ""} onChange={e => { setSaved(false); setValue({ ...value, periods: value.periods.map((p, n) => n === i ? { ...p, [key]: key === "grace_days" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value } : p) }); }} /></Field>)}
          {period.in_use && <span className="text-xs text-muted-foreground md:col-span-4">In use · period locked</span>}
        </div>)}
        {!value.periods.length && <p className="py-5 text-sm text-muted-foreground">No periods added.</p>}
      </div>
      {editable && <div className="flex flex-wrap items-center justify-between gap-3"><Button type="button" variant="outline" onClick={() => { setSaved(false); setValue({ ...value, periods: [...value.periods, { id: crypto.randomUUID(), label: "", start_date: "", end_date: "", grace_days: null }] }); }}><Plus className="size-4" />Add period</Button><Button loading={mutation.isPending}>Save</Button></div>}
    </fieldset>
    {mutation.isError && <Failure error={mutation.error} />}
    {saved && <p role="status" className="text-sm text-success">Saved.</p>}
  </form>;
}
