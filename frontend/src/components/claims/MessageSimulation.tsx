import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatError } from "@/lib/errors";

type TestMessage = { author: "member" | "broker"; body: string };
type Rendered = { id: string; author_name: string; body: string };
type Result = { recipient: string; member_view: Rendered[]; broker_view: Rendered[] };

export function MessageSimulation() {
  const year = useSession((s) => s.currentPolicyYearId);
  const [category, setCategory] = useState("outpatient");
  const [author, setAuthor] = useState<TestMessage["author"]>("broker");
  const [body, setBody] = useState("");
  const [messages, setMessages] = useState<TestMessage[]>([]);
  const simulation = useMutation({
    mutationFn: (next: TestMessage[]) => api.post<Result>(`/conversations/simulate?policy_year_id=${encodeURIComponent(year!)}`, { category, messages: next }),
  });
  const reset = () => { setMessages([]); setBody(""); simulation.reset(); };
  return <Card><CardHeader><CardTitle>Test message conversation</CardTitle></CardHeader><CardContent className="space-y-4">
    <p className="text-sm text-muted-foreground">Rehearse with a synthetic member. Messages stay in this simulation, do not reach real employees, and do not send email. Employee preview remains read-only.</p>
    <form className="space-y-3" onSubmit={(event) => {
      event.preventDefault();
      if (!body.trim() || messages.length >= 20) return;
      const next = [...messages, { author, body: body.trim() }];
      simulation.mutate(next, { onSuccess: () => { setMessages(next); setBody(""); } });
    }}>
      <div className="flex flex-wrap gap-3">
        <label className="text-sm">Claim category <select className="ml-2 rounded border border-input bg-background p-2" value={category} disabled={simulation.isPending} onChange={(event) => { setCategory(event.target.value); reset(); }}><option value="inpatient">Inpatient</option><option value="outpatient">Outpatient</option><option value="flex">Flex</option></select></label>
        <label className="text-sm">Test sender <select className="ml-2 rounded border border-input bg-background p-2" value={author} onChange={(event) => setAuthor(event.target.value as TestMessage["author"])}><option value="broker">Test adviser</option><option value="member">Test member</option></select></label>
      </div>
      <label className="block text-sm">Test message<textarea className="mt-1 block min-h-24 w-full rounded border border-input bg-background p-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" maxLength={2000} value={body} onChange={(event) => setBody(event.target.value)} /></label>
      <div className="flex gap-2"><Button type="submit" disabled={!year || !body.trim() || simulation.isPending || messages.length >= 20}>{simulation.isPending ? "Rendering…" : "Add test message"}</Button><Button type="button" variant="outline" disabled={simulation.isPending} onClick={reset}>Clear simulation</Button></div>
      {messages.length >= 20 && <p className="text-sm">Clear the simulation to start another conversation.</p>}
      {simulation.isError && <p role="alert" className="text-sm text-error">{formatError(simulation.error)}</p>}
    </form>
    {simulation.data && <div className="grid gap-4 md:grid-cols-2">{([['Member view', simulation.data.member_view], ['Broker view', simulation.data.broker_view]] as const).map(([title, rows]) => <section key={title} aria-label={title} className="rounded border border-border p-3"><h4 className="font-medium">{title}</h4><p className="text-xs text-muted-foreground">TEST-0001 · {simulation.data.recipient}</p><ol className="mt-3 space-y-3">{rows.map((message) => <li key={message.id} className="text-sm"><p className="font-medium">{message.author_name}</p><p className="whitespace-pre-wrap break-words">{message.body}</p></li>)}</ol></section>)}</div>}
  </CardContent></Card>;
}
