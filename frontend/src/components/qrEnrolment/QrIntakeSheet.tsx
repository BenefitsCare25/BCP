/** Review one QR submission: what was submitted, who it might be, and the
 *  decision — link it to a roster employee (it then joins the form register,
 *  like a scanned paper form) or reject it with a reason.
 *
 *  The body is keyed by the submission, so a roster search or a pending
 *  confirmation can never carry over from one submission to the next. */
import { useState } from "react";
import { Check, Download, Link2, Search, Undo2, XCircle } from "lucide-react";
import { toast } from "sonner";
import {
  downloadIntakePdf,
  searchCandidates,
  useIntakeAction,
  useQrIntake,
  type IntakeCandidate,
  type IntakeDetail,
  type IntakeItem,
  type RejectReason,
} from "@/api/qrEnrolment";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatError } from "@/lib/errors";
import { fmtDate, fmtDateTime } from "@/lib/format";

const REASONS: { value: RejectReason; label: string }[] = [
  { value: "duplicate", label: "Duplicate submission" },
  { value: "not_employee", label: "Not an employee of this company" },
  { value: "wrong_company", label: "Wrong company" },
  { value: "withdrawn", label: "Withdrawn by the employee" },
  { value: "other", label: "Other" },
];

const STATUS: Record<string, { label: string; variant: "warn" | "good" | "error" }> = {
  pending: { label: "Waiting to match", variant: "warn" },
  linked: { label: "Matched", variant: "good" },
  rejected: { label: "Rejected", variant: "error" },
};

type Action = ReturnType<typeof useIntakeAction>;

function money(v: number | null | undefined): string {
  return typeof v === "number"
    ? `S$${v.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "—";
}

function AsEntered({ d, item }: { d: IntakeDetail; item: IntakeItem }) {
  const rows = [
    ["Name", d.particulars.name], ["NRIC / FIN", item.id_masked], ["Staff ID", d.particulars.staff_id],
    ["Sex", d.particulars.gender], ["Mobile", d.particulars.contact_no], ["Email", d.particulars.email],
    ["Job grade", d.particulars.job_grade],
    ["Joined", d.particulars.date_of_hire ? fmtDate(d.particulars.date_of_hire) : null],
  ] as const;
  return (
    <section aria-labelledby="qr-particulars">
      <h3 id="qr-particulars" className="mb-2 text-sm font-semibold text-foreground">As entered</h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {rows.map(([term, value]) => (
          <div key={term} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{term}</dt>
            <dd className="truncate text-foreground">{value || "—"}</dd>
          </div>
        ))}
      </dl>
      {d.life_event && (
        <p className="mt-2 text-sm text-foreground">
          Life event: {d.life_event.kind.replace("_", " ")} on {fmtDate(d.life_event.date)}
        </p>
      )}
    </section>
  );
}

function Requested({ d }: { d: IntakeDetail }) {
  return (
    <>
      <section aria-labelledby="qr-requests">
        <h3 id="qr-requests" className="mb-2 text-sm font-semibold text-foreground">Requested</h3>
        <ul className="divide-y divide-border rounded-lg border border-border text-sm">
          {d.selections.map(s => (
            <li key={s.product_code} className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="font-medium text-foreground">{s.product_name ?? s.product_code}</p>
                <p className="text-muted-foreground">
                  {s.category ? `${s.category} · ` : ""}{s.elected_plan ?? "—"}
                  {s.covered?.length ? ` + ${s.covered.join(", ")}` : ""}
                  {s.withdrawn?.length ? ` · withdraw ${s.withdrawn.join(", ")}` : ""}
                </p>
              </div>
              {s.action === "enrol" && (
                <p className="shrink-0 text-right text-muted-foreground">
                  {s.composition} · share <span className="font-medium text-foreground">{money(s.you_pay)}</span>
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>
      {d.family.length > 0 && (
        <section aria-labelledby="qr-family">
          <h3 id="qr-family" className="mb-2 text-sm font-semibold text-foreground">Family</h3>
          <ul className="divide-y divide-border rounded-lg border border-border text-sm">
            {d.family.map(f => (
              <li key={f.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <span className="text-foreground">
                  {f.name} <span className="text-muted-foreground">· {f.relationship} · {fmtDate(f.dob)}</span>
                </span>
                <Badge variant="outline">{f.action_label}</Badge>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function CandidateRow({ c, busy, onLink }: { c: IntakeCandidate; busy: boolean; onLink: () => void }) {
  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-foreground">{c.employee_name ?? c.staff_id}</p>
        <p className="text-sm text-muted-foreground">
          {c.staff_id} · {c.id_masked ?? "—"}{c.category ? ` · ${c.category}` : ""}{c.status !== "active" ? ` · ${c.status}` : ""}
        </p>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {c.match.map(m => <Badge key={m} variant="good"><Check className="mr-1 size-3" />{m}</Badge>)}
          {c.existing_form && <Badge variant="outline">Has form {c.existing_form}</Badge>}
        </div>
      </div>
      <Button variant="outline" className="h-11 sm:h-9" onClick={onLink} disabled={busy}>
        <Link2 className="size-4" /> Link
      </Button>
    </li>
  );
}

function MatchSection({ item, suggested, act, run }: {
  item: IntakeItem;
  suggested: IntakeCandidate[];
  act: Action;
  run: (input: Parameters<Action["mutateAsync"]>[0], done: string) => Promise<void>;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<IntakeCandidate[] | null>(null);
  const [confirm, setConfirm] = useState<IntakeCandidate | null>(null);
  const candidates = found ?? suggested;
  const search = async () => {
    if (q.trim().length < 2) return;
    try { setFound(await searchCandidates(item.id, q.trim())); } catch (e) { toast.error(formatError(e)); }
  };
  return (
    <section aria-labelledby="qr-match" className="space-y-2">
      <h3 id="qr-match" className="text-sm font-semibold text-foreground">Match to an employee</h3>
      <form className="flex gap-2" onSubmit={e => { e.preventDefault(); void search(); }}>
        <Label htmlFor="qr-search" className="sr-only">Search the roster</Label>
        <Input id="qr-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search staff ID or name" />
        <Button type="submit" variant="outline" className="shrink-0"><Search className="size-4" /> Search</Button>
      </form>
      {candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No roster match. If this is a new joiner, ask HR to add them to the roster, then link.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {candidates.map(c => (
            <CandidateRow key={c.employee_id} c={c} busy={act.isPending} onLink={() => setConfirm(c)} />
          ))}
        </ul>
      )}
      {confirm && (
        <div className="space-y-2 rounded-lg border border-border bg-muted p-3 text-sm" role="alertdialog" aria-label="Confirm link">
          <p className="text-foreground">
            Link {item.reference_no} to <strong>{confirm.employee_name ?? confirm.staff_id}</strong> ({confirm.staff_id})?
            {confirm.existing_form ? ` Their current form ${confirm.existing_form} will be marked superseded.` : ""}
          </p>
          {!confirm.match.includes("NRIC / FIN") && (
            <p className="font-medium text-warn">
              The NRIC / FIN entered does not match this employee&apos;s record. Confirm with HR before linking.
            </p>
          )}
          <div className="flex gap-2">
            <Button loading={act.isPending} onClick={() => void run({
              kind: "link", id: item.id, employeeId: confirm.employee_id, confirmSupersede: !!confirm.existing_form,
            }, "Linked and filed")}>
              Link and file
            </Button>
            <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
          </div>
        </div>
      )}
    </section>
  );
}

function RejectSection({ item, act, run, onCancel }: {
  item: IntakeItem;
  act: Action;
  run: (input: Parameters<Action["mutateAsync"]>[0], done: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState<RejectReason>("duplicate");
  const [note, setNote] = useState("");
  return (
    <section className="space-y-3 rounded-lg border border-border p-3" aria-label="Reject submission">
      <div className="space-y-1.5">
        <Label htmlFor="qr-reason">Reason</Label>
        <NativeSelect id="qr-reason" value={reason} onChange={e => setReason(e.target.value as RejectReason)}>
          {REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
        </NativeSelect>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="qr-note">Note{reason === "other" ? "" : " (optional)"}</Label>
        <Input id="qr-note" value={note} maxLength={2000} onChange={e => setNote(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button variant="destructive" loading={act.isPending} disabled={reason === "other" && !note.trim()}
          onClick={() => void run({ kind: "reject", id: item.id, reason, note }, "Submission rejected")}>
          Reject
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </section>
  );
}

function IntakeBody({ intakeId, readOnly, onClose }: { intakeId: string; readOnly: boolean; onClose: () => void }) {
  const detail = useQrIntake(intakeId);
  const act = useIntakeAction();
  const [rejecting, setRejecting] = useState(false);
  const d = detail.data;
  const item = d?.item;
  const run = async (input: Parameters<Action["mutateAsync"]>[0], done: string) => {
    try {
      await act.mutateAsync(input);
      toast.success(done);
      if (input.kind === "restore") setRejecting(false);
      else onClose();
    } catch (e) {
      toast.error(formatError(e));
    }
  };
  return (
    <>
      <SheetHeader>
        <SheetTitle className="flex flex-wrap items-center gap-2">
          {item?.reference_no ?? "QR submission"}
          {item && <Badge variant={STATUS[item.status].variant}>{STATUS[item.status].label}</Badge>}
        </SheetTitle>
        <SheetDescription>
          {item ? `Submitted ${fmtDateTime(item.submitted_at)}${item.window_name ? ` · ${item.window_name}` : ""}. ` : ""}
          The details below are as the employee entered them; check them against HR records before linking.
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="space-y-5">
        {detail.isLoading || !d || !item ? (
          <Skeleton className="h-64 rounded-lg" />
        ) : (
          <>
            <AsEntered d={d} item={item} />
            <Requested d={d} />
            {item.status === "linked" && (
              <p className="rounded-lg bg-good-soft px-3 py-2 text-sm text-good">
                Linked to {item.linked_employee_name ?? "the employee"} and filed in the form register as {item.reference_no}.
                Enter the requested changes on the Members tab and confirm them as usual.
              </p>
            )}
            {item.status === "rejected" && (
              <p className="rounded-lg bg-error-soft px-3 py-2 text-sm text-error">Rejected: {item.rejected_reason}</p>
            )}
            {item.status === "pending" && !readOnly && (
              <MatchSection item={item} suggested={d.candidates} act={act} run={run} />
            )}
            {rejecting && <RejectSection item={item} act={act} run={run} onCancel={() => setRejecting(false)} />}
          </>
        )}
      </SheetBody>
      <SheetFooter className="flex-wrap">
        {item && (
          <Button variant="outline" onClick={() => void downloadIntakePdf(item).catch(e => toast.error(formatError(e)))}>
            <Download className="size-4" /> PDF
          </Button>
        )}
        {item?.status === "pending" && !readOnly && !rejecting && (
          <Button variant="destructiveOutline" onClick={() => setRejecting(true)}>
            <XCircle className="size-4" /> Reject
          </Button>
        )}
        {item?.status === "rejected" && !readOnly && (
          <Button variant="outline" loading={act.isPending} onClick={() => void run({ kind: "restore", id: item.id }, "Submission restored")}>
            <Undo2 className="size-4" /> Restore
          </Button>
        )}
      </SheetFooter>
    </>
  );
}

export function QrIntakeSheet({
  intakeId,
  readOnly,
  onClose,
}: {
  intakeId: string | null;
  readOnly: boolean;
  onClose: () => void;
}) {
  return (
    <Sheet open={!!intakeId} onOpenChange={open => { if (!open) onClose(); }}>
      <SheetContent>
        {intakeId && <IntakeBody key={intakeId} intakeId={intakeId} readOnly={readOnly} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  );
}
