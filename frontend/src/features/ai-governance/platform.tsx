import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileText,
  Info,
  Plus,
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
import { useAIReview } from "./review-state";
import {
  BackToOversight,
  Field,
  PreviewNotice,
  ReviewGate,
  Status,
  fieldClass,
  panelClass,
} from "./shared";

export function AIPlatformPage() {
  const revision = useAIReview((s) => s.revision);
  return (
    <ReviewGate platform>
      <PlatformWorkspace key={revision} />
    </ReviewGate>
  );
}
function PlatformWorkspace() {
  const release = useAIReview((s) => s.release);
  const search = useSearch({ strict: false }) as { tab?: string };
  const navigate = useNavigate();
  const tab = ["services", "releases", "suppliers", "actions"].includes(
    search.tab ?? "",
  )
    ? search.tab!
    : "releases";
  return (
    <div className="mx-auto max-w-screen-2xl">
      <PreviewNotice />
      <BackToOversight />
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">
          Platform AI oversight
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Shared services, validated releases and supplier evidence.
        </p>
      </div>
      <Tabs
        value={tab}
        onValueChange={(value) =>
          void navigate({
            to: "/platform/ai-oversight",
            search: { tab: value },
          })
        }
      >
        <TabsList
          aria-label="Platform oversight sections"
          className="mb-5 flex w-full max-w-full justify-start gap-3 overflow-x-auto sm:gap-6"
        >
          <TabsTrigger value="services">AI services</TabsTrigger>
          <TabsTrigger value="releases">Releases</TabsTrigger>
          <TabsTrigger value="suppliers">Data &amp; suppliers</TabsTrigger>
          <TabsTrigger value="actions">Actions</TabsTrigger>
        </TabsList>
        <TabsContent value="services" className="mt-0">
          <div className={panelClass}>
            {["Claim review", "Claim autofill", "Slip extraction"].map(
              (name, i) => (
                <div
                  key={name}
                  className="flex flex-wrap items-center justify-between gap-4 border-b border-border p-5 last:border-0"
                >
                  <div>
                    <h2 className="font-medium">{name}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Shared service · Current sample release{" "}
                      {i === 0 && release === "Active" ? "v3" : "v2"}
                    </p>
                  </div>
                  <Status value="Active" />
                  {i === 0 ? (
                    <Link
                      to="/platform/ai-oversight/releases/claim-review-v3"
                      className="inline-flex min-h-11 items-center gap-2 text-sm text-primary"
                    >
                      Review candidate
                      <ArrowRight className="size-4" aria-hidden="true" />
                    </Link>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      No candidate release
                    </span>
                  )}
                </div>
              ),
            )}
          </div>
        </TabsContent>
        <TabsContent value="releases" className="mt-0">
          <ReleaseReview />
        </TabsContent>
        <TabsContent value="suppliers" className="mt-0">
          <SupplierEvidence />
        </TabsContent>
        <TabsContent value="actions" className="mt-0">
          <div className={`${panelClass} p-6`}>
            <h2 className="font-semibold">Outstanding platform work</h2>
            <ul className="mt-5 space-y-5 text-sm">
              <li>
                <strong className="font-medium">
                  Independent claim-review validation
                </strong>
                <p className="mt-1 text-muted-foreground">
                  AI lead · Required before release approval
                </p>
              </li>
              <li>
                <strong className="font-medium">
                  Verify supplier commitments
                </strong>
                <p className="mt-1 text-muted-foreground">
                  Privacy owner · Retention and training-use terms
                </p>
              </li>
            </ul>
            <Button
              type="button"
              className="mt-6"
              variant="outline"
              onClick={() =>
                void navigate({
                  to: "/platform/ai-oversight",
                  search: { tab: "releases" },
                })
              }
            >
              Open release review
            </Button>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ReleaseReview() {
  const { evaluationAttached, reviewer, release, approvalReason, patch } =
    useAIReview();
  const [evaluationOpen, setEvaluationOpen] = useState(false);
  const [reason, setReason] = useState(approvalReason);
  const ready = evaluationAttached && Boolean(reviewer);
  const approved = release === "Approved" || release === "Active";
  function attach() {
    patch({
      evaluationAttached: true,
      release: reviewer ? "Awaiting approval" : "Blocked",
    });
    setEvaluationOpen(false);
    toast.success("Sample evaluation attached. No model was run.");
  }
  function approve(e: FormEvent) {
    e.preventDefault();
    if (!ready || !reason.trim()) return;
    patch({ release: "Approved", approvalReason: reason.trim() });
    toast.success("Release approval simulated. Activation is a separate step.");
  }
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">Claim review release</h2>
        <Status value={release} />
        <span className="text-sm text-muted-foreground">
          Candidate v3 {release !== "Active" && "· Not active"}
        </span>
      </div>
      {!approved && (
        <div className="mb-5 flex items-start gap-3 rounded-md border border-warn/25 bg-warn-soft p-4 text-sm text-warn">
          <AlertTriangle
            className="mt-0.5 size-5 shrink-0"
            aria-hidden="true"
          />
          <p>
            {!evaluationAttached
              ? "Independent validation is missing. This release cannot be approved."
              : !reviewer
                ? "Assign an independent reviewer before approving this release."
                : "Sample checks are complete. Review the evidence and record your approval reason."}
          </p>
        </div>
      )}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-5">
          <section className={`${panelClass} p-5`}>
            <h3 className="mb-5 font-semibold">Validation checks</h3>
            <div className="divide-y divide-border">
              {[
                ["Provider connection", "Passed", "Structured-output probe"],
                [
                  "Held-out evaluation",
                  evaluationAttached ? "Reviewed" : "Not measured",
                  evaluationAttached
                    ? "Sample dataset v1 · 40 adjudicated cases"
                    : "Attach evaluation results",
                ],
                [
                  "Independent reviewer",
                  reviewer ? "Assigned" : "Missing",
                  reviewer || "Reviewer required",
                ],
                [
                  "Configuration match",
                  "Current",
                  "Candidate manifest recorded",
                ],
              ].map(([label, status, detail]) => (
                <div
                  key={label}
                  className="grid gap-2 py-4 sm:grid-cols-[1.1fr_0.8fr_1.2fr] sm:items-center"
                >
                  <span className="text-sm font-medium">{label}</span>
                  <div>
                    <Status value={status} />
                  </div>
                  <span className="text-xs leading-relaxed text-muted-foreground">
                    {detail}
                  </span>
                </div>
              ))}
            </div>
          </section>
          <section className={`${panelClass} p-5`}>
            <h3 className="mb-5 font-semibold">Release details</h3>
            <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-5 gap-y-4 text-sm">
              <dt className="text-muted-foreground">Service</dt>
              <dd>Claim review</dd>
              <dt className="text-muted-foreground">Model</dt>
              <dd>Sample candidate model</dd>
              <dt className="text-muted-foreground">Prompt</dt>
              <dd>claim-review v3</dd>
              <dt className="text-muted-foreground">Dataset</dt>
              <dd>
                {evaluationAttached ? "sample-held-out-v1" : "Not linked"}
              </dd>
              <dt className="text-muted-foreground">Thresholds</dt>
              <dd>Sample conservative profile v1</dd>
            </dl>
          </section>
          {evaluationAttached && (
            <section className={`${panelClass} p-5`}>
              <h3 className="font-semibold">Sample evaluation evidence</h3>
              <p className="mt-2 text-xs text-muted-foreground">
                Illustrative results only · No live accuracy claim
              </p>
              <dl className="mt-5 grid grid-cols-2 gap-5 text-sm">
                <div>
                  <dt className="text-muted-foreground">Adjudicated cases</dt>
                  <dd className="mt-1 font-medium">40 / 40</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">
                    Incorrect clean results
                  </dt>
                  <dd className="mt-1 font-medium">0 / 24 clean results</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Unnecessary flags</dt>
                  <dd className="mt-1 font-medium">2 / 16 flagged results</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Coverage</dt>
                  <dd className="mt-1">English invoices · GP visits</dd>
                </div>
              </dl>
              <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
                This sample does not establish performance for other languages
                or claim types. Acceptance criteria and representative coverage
                require the risk owner’s approval.
              </p>
            </section>
          )}
        </div>
        <div className="space-y-5">
          <section className={`${panelClass} p-5`}>
            <h3 className="mb-5 text-lg font-semibold">Approval</h3>
            <p className="mb-5 text-sm">
              <span className="text-muted-foreground">Prepared by:</span> AI
              lead
            </p>
            <form className="space-y-5" onSubmit={approve}>
              <Field label="Independent approver">
                <select
                  className={fieldClass}
                  value={reviewer}
                  disabled={approved}
                  onChange={(e) =>
                    patch({
                      reviewer: e.target.value,
                      release:
                        evaluationAttached && e.target.value
                          ? "Awaiting approval"
                          : "Blocked",
                    })
                  }
                >
                  <option value="">Unassigned</option>
                  <option>Independent claims reviewer</option>
                </select>
              </Field>
              <Field label="Approval reason">
                <textarea
                  className={fieldClass}
                  rows={3}
                  required
                  value={reason}
                  disabled={approved}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Summarize the evidence and remaining limits."
                />
              </Field>
              <Button
                type="submit"
                className="w-full"
                disabled={!ready || !reason.trim() || approved}
              >
                <CheckCircle2 className="size-4" aria-hidden="true" />
                {approved ? "Approval recorded in preview" : "Approve release"}
              </Button>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {approved
                  ? "Any material change to the candidate requires a new approval."
                  : "Complete validation and independent review first. The preparer cannot approve their own release."}
              </p>
            </form>
            {!approved && (
              <Button
                type="button"
                variant="outline"
                className="mt-5 w-full"
                onClick={() => setEvaluationOpen(true)}
              >
                <Plus className="size-4" aria-hidden="true" />
                {evaluationAttached
                  ? "View sample evaluation"
                  : "Add evaluation evidence"}
              </Button>
            )}
          </section>
          <section className={`${panelClass} p-5`}>
            <h3 className="font-semibold">Current deployment</h3>
            <p className="mt-4 flex items-start gap-2 text-sm leading-relaxed text-muted-foreground">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {release === "Active"
                ? "Sample release v3 is active in this preview."
                : "Existing sample release v2 remains active."}
            </p>
            {release === "Approved" && (
              <>
                <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                  Activation would apply to shared claim review. The previous
                  approved release and manual handling remain the fallback.
                </p>
                <Button
                  type="button"
                  className="mt-5 w-full"
                  onClick={() => {
                    patch({ release: "Active" });
                    toast.success(
                      "Activation simulated in this browser tab only.",
                    );
                  }}
                >
                  Preview activation
                </Button>
              </>
            )}
            {release === "Active" && (
              <Button
                type="button"
                variant="outline"
                className="mt-5 w-full"
                onClick={() => {
                  patch({ release: "Approved" });
                  toast.success(
                    "Returned to sample release v2 in the preview.",
                  );
                }}
              >
                Preview rollback to v2
              </Button>
            )}
          </section>
        </div>
      </div>
      <Sheet open={evaluationOpen} onOpenChange={setEvaluationOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Evaluation evidence</SheetTitle>
            <SheetDescription>
              Use the sample below to check the release approval interaction.
            </SheetDescription>
          </SheetHeader>
          <SheetBody className="space-y-5">
            <p className="text-sm leading-relaxed text-muted-foreground">
              Production evaluation will need a protected held-out dataset,
              independent adjudication and acceptance criteria approved before
              testing.
            </p>
            <div className="rounded-md bg-muted p-4 text-sm">
              <p className="font-medium">Sample held-out evaluation v1</p>
              <p className="mt-2">40 cases · English GP invoices</p>
              <p className="mt-2 text-xs text-muted-foreground">
                These illustrative counts demonstrate the UI; they are not
                results from your platform.
              </p>
            </div>
            <Button type="button" onClick={attach}>
              Attach sample evaluation
            </Button>
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  );
}

function SupplierEvidence() {
  const { supplierReference, supplierNotes, patch } = useAIReview();
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState(supplierReference);
  const [notes, setNotes] = useState(supplierNotes);
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">Google Vertex AI</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Claim intake, claim review and configured extraction
          </p>
        </div>
        <Status value="Action required" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section className={`${panelClass} p-5`}>
          <h3 className="font-semibold">Processing and evidence</h3>
          <dl className="my-6 space-y-4 text-sm">
            <div>
              <dt className="text-muted-foreground">
                Configured processing endpoint
              </dt>
              <dd className="mt-1">Singapore · Sample configuration</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Data categories</dt>
              <dd className="mt-1">
                Claim documents, health information and identifiers
              </dd>
            </div>
          </dl>
          <div className="divide-y divide-border">
            {[
              [
                "Processing agreement",
                supplierReference
                  ? "Reference added · Needs review"
                  : "Not linked",
              ],
              ["Retention and training-use terms", "Needs verification"],
              ["Inspro retention schedule", "Awaiting approval"],
              ["Deletion and backup procedure", "Not linked"],
            ].map(([label, state]) => (
              <div
                key={label}
                className="flex flex-wrap justify-between gap-3 py-4 text-sm"
              >
                <span>{label}</span>
                <span className="text-muted-foreground">{state}</span>
              </div>
            ))}
          </div>
          {supplierReference && (
            <p className="mt-4 break-words text-sm">
              <FileText className="mr-2 inline size-4" aria-hidden="true" />
              {supplierReference}
            </p>
          )}
        </section>
        <section className={`${panelClass} self-start p-5`}>
          <h3 className="font-semibold">Next action</h3>
          <p className="mt-4 text-sm">Verify provider terms</p>
          <p className="mt-2 text-xs text-muted-foreground">
            Owner: Privacy owner
          </p>
          <p className="mt-5 text-sm leading-relaxed text-muted-foreground">
            A configured endpoint is separate from a verified supplier
            commitment. Unknown terms remain unresolved.
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-5 w-full"
            onClick={() => {
              setReference(supplierReference);
              setNotes(supplierNotes);
              setOpen(true);
            }}
          >
            Add evidence reference
          </Button>
        </section>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Supplier evidence reference</SheetTitle>
            <SheetDescription>
              Adding a reference does not approve the terms.
            </SheetDescription>
          </SheetHeader>
          <SheetBody>
            <form
              className="space-y-5"
              onSubmit={(e) => {
                e.preventDefault();
                if (!reference.trim()) return;
                patch({
                  supplierReference: reference.trim(),
                  supplierNotes: notes,
                });
                setOpen(false);
                toast.success(
                  "Reference added to this preview. Verification remains open.",
                );
              }}
            >
              <Field label="Document reference">
                <Input
                  required
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                />
              </Field>
              <Field label="What needs verification?">
                <textarea
                  rows={4}
                  className={fieldClass}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </Field>
              <Button type="submit">Save reference</Button>
            </form>
          </SheetBody>
        </SheetContent>
      </Sheet>
    </>
  );
}
