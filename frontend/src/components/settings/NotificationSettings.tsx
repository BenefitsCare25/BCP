import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSession } from "@/stores/session";
import { toast } from "sonner";

type Settings = { claim_delivery: "immediate" | "digest"; digest_minutes: number; revision: number };
const path = "/workflow-notifications/settings";

export function NotificationSettings() {
  const clientId = useSession(s => s.activeClientId);
  return clientId ? <SettingsLoader key={clientId} clientId={clientId} /> : <p>Select a company.</p>;
}

function SettingsLoader({ clientId }: { clientId: string }) {
  const query = useQuery({
    queryKey: ["notification-settings", clientId],
    queryFn: () => api.get<Settings>(path, { headers: { "X-Inspro-Client": clientId } }),
  });
  if (query.isPending) return <p role="status" className="text-sm text-muted-foreground">Loading notification settings…</p>;
  if (query.isError) return <div role="alert"><p>{query.error.message}</p><Button variant="outline" onClick={() => void query.refetch()}>Retry</Button></div>;
  return <SettingsForm key={query.data.revision} initial={query.data} clientId={clientId} />;
}

function SettingsForm({ initial, clientId }: { initial: Settings; clientId: string }) {
  const { data: me } = useMe();
  const editable = me?.role === "broker_admin" || me?.role === "system_admin";
  const [value, setValue] = useState(initial);
  const [saved, setSaved] = useState(false);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => api.put<Settings>(path, value, { headers: { "X-Inspro-Client": clientId } }),
    onSuccess: result => {
      setValue(result);
      setSaved(true);
      toast.success("Notification settings saved.");
      queryClient.setQueryData(["notification-settings", clientId], result);
    },
  });
  return <form className="max-w-xl space-y-5" onSubmit={event => { event.preventDefault(); mutation.mutate(); }}>
    <div>
      <h2 className="font-semibold">Employee claim notifications</h2>
      <p className="mt-1 text-sm text-muted-foreground">Choose how employees receive updates across their claims. Each conversation stays separate in the portal.</p>
    </div>
    <fieldset disabled={!editable || mutation.isPending} className="space-y-4">
      <div className="space-y-2">
        <label htmlFor="claim-delivery" className="block text-sm font-medium">Delivery</label>
        <select id="claim-delivery" className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring"
          value={value.claim_delivery} onChange={event => { setSaved(false); setValue({ ...value, claim_delivery: event.target.value as Settings["claim_delivery"] }); }}>
          <option value="immediate">Send each update immediately</option>
          <option value="digest">Combine updates into a digest</option>
        </select>
      </div>
      {value.claim_delivery === "digest" && <div className="space-y-2">
        <label htmlFor="digest-minutes" className="block text-sm font-medium">Digest interval (minutes)</label>
        <Input id="digest-minutes" type="number" min={15} max={1440} required className="max-w-32"
          value={value.digest_minutes} onChange={event => { setSaved(false); setValue({ ...value, digest_minutes: Number(event.target.value) }); }} />
        <p className="text-xs text-muted-foreground">Updates are grouped in fixed windows of 15–1,440 minutes, with a separate sign-in link for each claim.</p>
      </div>}
      <p className="text-sm text-muted-foreground">Requests for information are urgent and always sent immediately. Changes apply to new updates; already queued notifications keep their delivery schedule.</p>
      {editable && <Button loading={mutation.isPending}>Save notification settings</Button>}
    </fieldset>
    {mutation.isError && <div role="alert" className="space-y-2 text-sm text-error"><p>{mutation.error.message}</p><Button type="button" variant="outline" onClick={() => void queryClient.invalidateQueries({ queryKey: ["notification-settings", clientId] })}>Reload settings</Button></div>}
    {saved && <p role="status" className="text-sm text-success">Notification settings saved.</p>}
  </form>;
}
