import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  ArrowRight,
  Check,
  FileText,
  Plus,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetBody,
} from "@/components/ui/sheet";
import { cn } from "@/lib/cn";
import { useAIReview, type AIUse, type Evidence } from "./review-state";
import {
  Field,
  PreviewNotice,
  ReviewGate,
  Status,
  WorkflowLinks,
  fieldClass,
  panelClass,
} from "./shared";

export function AIOversightPage() {
  const revision = useAIReview((s) => s.revision);
  return (
    <ReviewGate>
      <OversightWorkspace key={revision} />
    </ReviewGate>
  );
}

function OversightWorkspace() {
  const { uses, actions, evidence, updateAction } = useAIReview();
  const search = useSearch({ strict: false }) as {
    tab?: string;
    use?: string;
    action?: string;
  };
  const navigate = useNavigate();
  const tab = ["uses", "actions", "evidence"].includes(search.tab ?? "")
    ? search.tab!
    : "uses";
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All states");
  const [editingUse, setEditingUse] = useState<AIUse | "new" | null>(null);
  const [editingEvidence, setEditingEvidence] = useState<Evidence | null>(null);
  const [detailClosed, setDetailClosed] = useState(false);
  const showDetail = Boolean(search.use) || !detailClosed;
  const selected = uses.find((u) => u.id === (search.use ?? "claim-review"));
  const go = (next: { tab?: string; use?: string; action?: string }) =>
    void navigate({ to: "/firm/ai-oversight", search: next });
  const openNextAction = (record: AIUse) => {
    if (record.nextActionId)
      go({ tab: "actions", action: record.nextActionId });
    else setEditingUse(record);
  };
  const filtered = uses.filter(
    (u) =>
      `${u.name} ${u.owner} ${u.purpose}`
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (filter === "All states" ||
        (filter === "Needs attention"
          ? u.review !== "Reviewed"
          : u.review === filter)),
  );
  return (
    <div className="mx-auto max-w-screen-2xl">
      <PreviewNotice />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            AI oversight
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Know what AI does, who owns it, and what needs attention.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="size-4" aria-hidden="true" />
          Firm-wide · Demo brokerage
        </div>
      </div>
      <Tabs value={tab} onValueChange={(value) => go({ tab: value })}>
        <TabsList
          aria-label="AI oversight sections"
          className="mb-5 flex w-full justify-start gap-6"
        >
          <TabsTrigger value="uses">AI uses</TabsTrigger>
          <TabsTrigger value="actions">
            Actions{" "}
            <span className="ml-2 rounded bg-muted px-1.5 text-xs">
              {actions.filter((a) => a.status !== "Complete").length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
        </TabsList>
        <TabsContent value="uses" className="mt-0">
          <div className="mb-5 flex flex-wrap gap-3">
            <div className="relative min-w-0 flex-1 basis-56">
              <Search
                className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                aria-label="Find an AI use"
                placeholder="Find an AI use or owner"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="h-10 pl-9"
              />
            </div>
            <select
              aria-label="Filter evidence state"
              className={cn(fieldClass, "h-10 w-auto max-w-full")}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              {[
                "All states",
                "Needs attention",
                "Action required",
                "Awaiting review",
                "Not assessed",
                "Reviewed",
              ].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <Button
              type="button"
              className="h-10"
              onClick={() => setEditingUse("new")}
            >
              <Plus className="size-4" aria-hidden="true" />
              Register an AI use
            </Button>
          </div>
          <div
            className={cn(
              "grid items-start gap-5",
              showDetail && "xl:grid-cols-[minmax(0,1fr)_360px]",
            )}
          >
            <div
              className={cn(
                panelClass,
                "min-w-0 overflow-hidden",
                search.use && "hidden xl:block",
              )}
            >
              <div className="hidden grid-cols-[1.3fr_1fr_1fr_1.15fr] gap-4 border-b border-border bg-muted/40 px-5 py-3 text-xs font-medium text-muted-foreground md:grid">
                <span>AI use / purpose</span>
                <span>Owner</span>
                <span>Deployment</span>
                <span>Evidence</span>
              </div>
              <ul
                aria-label="Registered AI uses"
                className="divide-y divide-border"
              >
                {filtered.map((u) => (
                  <li
                    key={u.id}
                    className={cn(
                      selected?.id === u.id
                        ? "bg-accent/45"
                        : "hover:bg-muted/40",
                    )}
                  >
                    <div className="grid gap-4 px-5 pt-5 md:grid-cols-[1.3fr_1fr_1fr_1.15fr] md:items-start">
                      <div>
                        <Link
                          to="/firm/ai-oversight"
                          search={{ use: u.id }}
                          className="inline-flex min-h-6 items-center text-sm font-semibold text-foreground hover:text-primary hover:underline"
                        >
                          {u.name}
                        </Link>
                        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                          {u.id === "claim-review"
                            ? "Claims assessment"
                            : u.id === "claim-autofill"
                              ? "Member intake"
                              : "Benefit setup"}
                        </p>
                      </div>
                      <span className="text-sm">
                        <span className="mr-2 text-xs text-muted-foreground md:hidden">
                          Owner
                        </span>
                        {u.owner}
                      </span>
                      <div>
                        <span className="mr-2 text-xs text-muted-foreground md:hidden">
                          Deployment
                        </span>
                        <Status value={u.deployment} />
                      </div>
                      <div>
                        <span className="mr-2 text-xs text-muted-foreground md:hidden">
                          Evidence
                        </span>
                        <Status value={u.review} />
                      </div>
                    </div>
                    <button
                      type="button"
                      className="flex min-h-11 w-full items-center justify-between gap-3 px-5 py-3 text-left text-xs text-muted-foreground hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                      onClick={() => openNextAction(u)}
                    >
                      <span>Next: {u.nextAction}</span>
                      <ArrowRight
                        className="size-3.5 shrink-0"
                        aria-hidden="true"
                      />
                    </button>
                  </li>
                ))}
              </ul>
              {!filtered.length && (
                <div className="p-8 text-center">
                  <h2 className="font-medium">No matching AI uses</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Try another name or clear your filters.
                  </p>
                  <Button
                    type="button"
                    variant="link"
                    onClick={() => {
                      setQuery("");
                      setFilter("All states");
                    }}
                  >
                    Clear filters
                  </Button>
                </div>
              )}
              <p className="border-t border-border px-5 py-3 text-xs text-muted-foreground">
                {filtered.length} of {uses.length} uses · Deployment and
                evidence review are separate.
              </p>
            </div>
            {showDetail && (
              <section
                aria-label="Selected AI use"
                className={cn(
                  panelClass,
                  "min-w-0",
                  !search.use && "hidden xl:block",
                )}
              >
                {selected ? (
                  <UseDetail
                    key={selected.id}
                    record={selected}
                    onClose={() => {
                      setDetailClosed(true);
                      go({});
                    }}
                    onEdit={() => setEditingUse(selected)}
                    onAction={() => openNextAction(selected)}
                    onEvidence={() => go({ tab: "evidence" })}
                  />
                ) : (
                  <div className="p-6">
                    <h2 className="font-medium">AI use not found</h2>
                    <Button type="button" variant="link" onClick={() => go({})}>
                      Return to the register
                    </Button>
                  </div>
                )}
              </section>
            )}
          </div>
        </TabsContent>
        <TabsContent value="actions" className="mt-0">
          <div className="mb-5">
            <h2 className="font-semibold">Assigned actions</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Give every unresolved issue an owner and a next step.
            </p>
          </div>
          {search.action && (
            <Button
              type="button"
              variant="link"
              className="mb-3"
              onClick={() => go({ tab: "actions" })}
            >
              Show all actions
            </Button>
          )}
          <div className={cn(panelClass, "divide-y divide-border")}>
            {search.action && !actions.some((a) => a.id === search.action) && (
              <p role="status" className="p-5 text-sm">
                Action not found. Show all actions to continue.
              </p>
            )}
            {actions
              .filter((a) => !search.action || a.id === search.action)
              .map((a) => (
                <section
                  key={a.id}
                  className="grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_170px_145px_auto] lg:items-center"
                >
                  <div>
                    <h3 className="text-sm font-medium">{a.title}</h3>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Review due {a.due}
                    </p>
                  </div>
                  <Field label="Owner">
                    <select
                      aria-label={`Owner for ${a.title}`}
                      className={fieldClass}
                      value={a.owner}
                      onChange={(e) =>
                        updateAction(a.id, { owner: e.target.value })
                      }
                    >
                      {[
                        "Unassigned",
                        "AI lead",
                        "Claims lead",
                        "Product lead",
                        "Privacy owner",
                      ].map((o) => (
                        <option key={o}>{o}</option>
                      ))}
                    </select>
                  </Field>
                  <Status value={a.status} />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      updateAction(a.id, {
                        status: a.status === "Complete" ? "Open" : "Complete",
                      });
                      toast.success("Action updated in this preview.");
                    }}
                  >
                    {a.status === "Complete" ? "Reopen" : "Mark complete"}
                  </Button>
                </section>
              ))}
          </div>
        </TabsContent>
        <TabsContent value="evidence" className="mt-0">
          <div className="mb-5">
            <h2 className="font-semibold">Policies and evidence</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Keep the exact document version, owner and reviewer together.
            </p>
          </div>
          <div className={cn(panelClass, "divide-y divide-border")}>
            {evidence.map((e) => (
              <button
                key={e.id}
                type="button"
                className="flex w-full flex-wrap items-center justify-between gap-5 p-5 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                onClick={() => setEditingEvidence(e)}
              >
                <span className="flex items-center gap-3">
                  <FileText
                    className="size-5 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span>
                    <strong className="text-sm font-medium">{e.title}</strong>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {e.version} · {e.owner}
                    </span>
                  </span>
                </span>
                <span className="flex items-center gap-5">
                  <Status value={e.status} />
                  <ArrowRight className="size-4" aria-hidden="true" />
                </span>
              </button>
            ))}
          </div>
          <p className="mt-4 max-w-2xl text-xs leading-relaxed text-muted-foreground">
            Approval records the reviewer and the specific version. Reviewed
            evidence does not indicate ISO certification.
          </p>
        </TabsContent>
      </Tabs>
      <WorkflowLinks />
      <Sheet
        open={editingUse !== null}
        onOpenChange={(open) => {
          if (!open) setEditingUse(null);
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>
              {editingUse === "new" ? "Register an AI use" : "Edit AI use"}
            </SheetTitle>
            <SheetDescription>
              Registration records the intended use. It does not enable AI
              processing.
            </SheetDescription>
          </SheetHeader>
          <SheetBody>
            {editingUse && (
              <UseForm
                key={editingUse === "new" ? "new" : editingUse.id}
                record={editingUse}
                onDone={() => setEditingUse(null)}
              />
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
      <Sheet
        open={editingEvidence !== null}
        onOpenChange={(open) => {
          if (!open) setEditingEvidence(null);
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{editingEvidence?.title}</SheetTitle>
            <SheetDescription>
              Firm evidence · Changes are saved only in this local review.
            </SheetDescription>
          </SheetHeader>
          <SheetBody>
            {editingEvidence && (
              <EvidenceForm
                key={editingEvidence.id}
                record={editingEvidence}
                onDone={() => setEditingEvidence(null)}
              />
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function UseDetail({
  record,
  onClose,
  onEdit,
  onAction,
  onEvidence,
}: {
  record: AIUse;
  onClose: () => void;
  onEdit: () => void;
  onAction: () => void;
  onEvidence: () => void;
}) {
  return (
    <>
      <div className="flex items-center justify-between px-5 pt-5">
        <h2 className="text-lg font-semibold">{record.name}</h2>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Back to AI use register"
          onClick={onClose}
        >
          <X className="size-4" aria-hidden="true" />
        </Button>
      </div>
      <Tabs defaultValue="purpose" className="p-5">
        <TabsList aria-label="AI use details" className="w-full gap-4">
          <TabsTrigger value="purpose">Purpose</TabsTrigger>
          <TabsTrigger value="risks">Risks</TabsTrigger>
          <TabsTrigger value="evidence">Evidence</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
        <TabsContent value="purpose">
          <p className="mb-6 mt-5 text-sm leading-relaxed text-muted-foreground">
            {record.purpose}
          </p>
          <dl className="space-y-5 text-sm">
            {[
              ["Owner", record.owner],
              ["Affected people", "Members and dependants"],
              ["Review due", "15 Oct 2026"],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-right">{value}</dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-4">
              <dt className="text-muted-foreground">Deployment</dt>
              <dd>
                <Status value={record.deployment} />
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-muted-foreground">Evidence</dt>
              <dd>
                <Status value={record.review} />
              </dd>
            </div>
          </dl>
          <p className="mt-6 rounded-md bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">
            Manual alternative: an authorized person checks the source documents
            and records the outcome.
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-5 w-full"
            onClick={onEdit}
          >
            Edit owner and purpose
          </Button>
        </TabsContent>
        <TabsContent value="risks" className="space-y-5 pt-3">
          <div>
            <h3 className="text-sm font-medium">Potential harm</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {record.risk}
            </p>
          </div>
          <div>
            <h3 className="text-sm font-medium">Current safeguard</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {record.control}
            </p>
          </div>
          <Status value="Action required" />
          <p className="text-xs leading-relaxed text-muted-foreground">
            Residual risk needs a recorded assessment and an accountable owner’s
            decision.
          </p>
        </TabsContent>
        <TabsContent value="evidence" className="space-y-4 pt-3">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Policy, validation and staff readiness records support this AI use.
          </p>
          <Button type="button" variant="outline" onClick={onEvidence}>
            Open evidence register
          </Button>
        </TabsContent>
        <TabsContent value="history" className="space-y-3 pt-3">
          <p className="text-sm font-medium">Sample record created</p>
          <p className="text-xs text-muted-foreground">
            28 Sep 2026 · Demo administrator
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            This local review has no persisted audit log. Production history
            will record actor, version and time.
          </p>
        </TabsContent>
      </Tabs>
      <div className="border-t border-border p-5">
        <h3 className="text-sm font-semibold">Next action</h3>
        <p className="mt-3 text-sm">{record.nextAction}</p>
        <p className="mt-2 text-xs text-muted-foreground">
          Owner: {record.owner}
        </p>
        <Button type="button" className="mt-5" onClick={onAction}>
          Open action
          <ArrowRight className="size-4" aria-hidden="true" />
        </Button>
      </div>
    </>
  );
}
function UseForm({
  record,
  onDone,
}: {
  record: AIUse | "new";
  onDone: () => void;
}) {
  const store = useAIReview();
  const existing = record === "new" ? undefined : record;
  const [name, setName] = useState(existing?.name ?? "");
  const [purpose, setPurpose] = useState(existing?.purpose ?? "");
  const [owner, setOwner] = useState(existing?.owner ?? "Unassigned");
  const [error, setError] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !purpose.trim()) {
      setError("Enter a name and a clear intended purpose.");
      return;
    }
    if (existing)
      store.updateUse(existing.id, {
        name: name.trim(),
        purpose: purpose.trim(),
        owner,
      });
    else
      store.addUse({
        id: `sample-${crypto.randomUUID()}`,
        name: name.trim(),
        purpose: purpose.trim(),
        owner,
        deployment: "Manual handling",
        review: "Not assessed",
        nextAction: "Assess purpose and risks before use",
        risk: "Risk assessment not recorded.",
        control: "Manual handling until the use is assessed.",
      });
    toast.success("AI use saved in this preview.");
    onDone();
  }
  return (
    <form onSubmit={submit} className="space-y-5">
      <Field label="AI use name">
        <Input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Intended purpose">
        <textarea
          required
          rows={4}
          className={fieldClass}
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        />
      </Field>
      <Field label="Accountable owner">
        <select
          className={fieldClass}
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
        >
          {[
            "Unassigned",
            "Claims lead",
            "Product lead",
            "Privacy owner",
            "AI lead",
          ].map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
      </Field>
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-3 border-t border-border pt-5">
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit">Save use</Button>
      </div>
    </form>
  );
}
function EvidenceForm({
  record,
  onDone,
}: {
  record: Evidence;
  onDone: () => void;
}) {
  const update = useAIReview((s) => s.updateEvidence);
  const [reference, setReference] = useState(record.reference);
  const [reviewer, setReviewer] = useState(record.reviewer);
  const [error, setError] = useState("");
  const ready = Boolean(reference.trim() && reviewer);
  function save(request: boolean) {
    if (request && !ready) {
      setError("Add a document reference and assign a reviewer first.");
      return;
    }
    update(record.id, {
      reference: reference.trim(),
      reviewer,
      version: record.version === "Not recorded" ? "Draft 1" : record.version,
      status: request ? "Awaiting approval" : "Draft",
      history: [
        ...record.history,
        request
          ? "Review requested in the local preview."
          : "Draft saved in the local preview.",
      ],
    });
    toast.success(
      request
        ? "Evidence marked for review in this preview."
        : "Draft saved in this preview.",
    );
    onDone();
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save(false);
      }}
      className="space-y-5"
    >
      <div className="flex justify-between">
        <Status value={record.status} />
        <span className="text-sm text-muted-foreground">{record.version}</span>
      </div>
      <Field
        label="Document reference"
        hint="Use a document name or approved repository reference. No document is uploaded in this preview."
      >
        <Input
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          placeholder="AI-policy-draft.pdf"
        />
      </Field>
      <Field label="Reviewer">
        <select
          className={fieldClass}
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        >
          <option value="">Unassigned</option>
          <option>Independent reviewer</option>
          <option>Privacy owner</option>
        </select>
      </Field>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {ready
          ? "Ready to request a review of this version."
          : "Add a document reference and assign a reviewer before requesting approval."}
      </p>
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <Button type="submit" variant="outline">
          Save draft
        </Button>
        <Button type="button" disabled={!ready} onClick={() => save(true)}>
          Request approval
        </Button>
      </div>
      <section className="border-t border-border pt-5">
        <h3 className="mb-3 text-sm font-semibold">Version history</h3>
        {record.history.length ? (
          <ul className="space-y-3">
            {record.history.map((entry, i) => (
              <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                <Check className="size-3.5 shrink-0" aria-hidden="true" />
                {entry}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            No evidence recorded yet.
          </p>
        )}
      </section>
    </form>
  );
}
