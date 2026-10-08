import { toast } from "sonner";
import { useEnrollmentEvents, useRetryEnrollmentEmail } from "@/api/enrollmentEvents";
import { sendErrorMessage } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { fmtWhen } from "@/components/enrollment/period/periodMeta";

const EMAIL_STATUS: Record<string, string> = {
  queued: "Email queued", sending: "Email sending", sent: "Email accepted by mail provider",
  dead: "Email failed", cancelled: "Email cancelled", unavailable: "Email unavailable",
};

export function EnrollmentActivity({ id, readOnly }: { id: string; readOnly: boolean }) {
  const events = useEnrollmentEvents(id);
  const retryEmail = useRetryEnrollmentEmail(id);
  if (events.isPending) return <p className="text-sm text-muted-foreground">Loading enrolment activity…</p>;
  if (events.isError) return <Button variant="outline" onClick={() => void events.refetch()}>Retry enrolment activity</Button>;
  if (!events.data.length) return null;
  return <section aria-label="Enrolment activity" className="space-y-3">
    <h3 className="text-sm font-semibold">Enrolment activity</h3>
    <ol className="space-y-3">
      {events.data.map((event) => <li key={event.id} className="space-y-1 text-sm">
        <p className="font-medium">{event.title}</p>
        {event.reason && <p className="whitespace-pre-wrap break-words">{event.reason}</p>}
        <p className="text-xs text-muted-foreground">{fmtWhen(event.created_at)} · Portal notice recorded · {EMAIL_STATUS[event.email_status] ?? event.email_status}</p>
        {event.email_detail && <p className="text-xs text-muted-foreground">{event.email_detail}</p>}
        {!readOnly && ["dead", "cancelled", "unavailable"].includes(event.email_status) &&
          <Button variant="outline" size="sm" disabled={retryEmail.isPending} onClick={() => retryEmail.mutate(event.id, { onError: (error) => toast.error(sendErrorMessage(error)) })}>Retry email notification</Button>}
      </li>)}
    </ol>
  </section>;
}
