import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

interface ActivityRow {
  actor_id: string; actor_type: string; name: string; claims_handled: number;
  actions: number; repeat_actions: number; average_decision_hours: number | null;
  decision_samples: number; by_status: Record<string, number>;
}

export function ServicerActivity() {
  const client = useSession((s) => s.activeClientId);
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore" }).format(new Date());
  const [from, setFrom] = useState(`${today.slice(0, 7)}-01`);
  const [to, setTo] = useState(today);
  const valid = !!from && !!to && from <= to;
  const result = useQuery({
    queryKey: ["claim-workload", client, from, to],
    queryFn: () => api.get<{ items: ActivityRow[] }>(`/audit-log/claim-workload?from_date=${from}&to_date=${to}`),
    enabled: !!client && valid,
  });
  return (
    <section className="space-y-3" aria-labelledby="servicer-activity-title">
      <h3 id="servicer-activity-title" className="font-semibold text-foreground">Servicer activity</h3>
      <p className="text-sm text-muted-foreground">
        Recorded claims work across benefit years in this company. Dates use Singapore time.
        Counts describe actions performed, not case ownership or a productivity score.
      </p>
      <div className="flex flex-wrap gap-3">
        <div><Label htmlFor="work-from">From</Label><Input id="work-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
        <div><Label htmlFor="work-to">To</Label><Input id="work-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
      </div>
      {!valid && <p role="alert" className="text-sm text-error">Choose a start date on or before the end date.</p>}
      {result.isLoading && <p role="status">Loading activity…</p>}
      {result.isError && <div role="alert"><p>Activity could not be loaded. Choose a range of at most one year.</p><Button variant="outline" onClick={() => void result.refetch()}>Retry</Button></div>}
      {result.isSuccess && valid && (result.data.items.length ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-border text-left">
              {["Servicer", "Claims", "Actions", "Approved", "Rejected", "Needs info", "Paid", "Repeat actions", "Decision hours"].map((label) => <th key={label} scope="col" className="whitespace-nowrap px-3 py-2">{label}</th>)}
            </tr></thead>
            <tbody>{result.data.items.map((row) => <tr key={`${row.actor_type}:${row.actor_id}`} className="border-b border-border last:border-0">
              <th scope="row" className="px-3 py-2 text-left font-medium">{row.name}<span className="block text-xs font-normal text-muted-foreground">{row.actor_type === "human" ? "Human" : "Automation"}</span></th>
              {[row.claims_handled, row.actions, row.by_status.approved ?? 0, row.by_status.rejected ?? 0, row.by_status.needs_info ?? 0, row.by_status.paid ?? 0, row.repeat_actions, row.average_decision_hours ?? "—"].map((value, i) => <td key={i} className="px-3 py-2 text-right tabular-nums">{value}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
      ) : <p className="text-sm text-muted-foreground">No recorded claims work in this period.</p>)}
      <p className="text-xs text-muted-foreground">Decision hours average approval/rejection time from the recorded submission. Repeat actions can include normal follow-up; they do not necessarily indicate rework. Events without recorded audit history cannot be reconstructed.</p>
    </section>
  );
}
