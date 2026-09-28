import { useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  FileText,
  MessageSquare,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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

export function AIMemberJourneyPage() {
  const revision = useAIReview((s) => s.revision);
  return (
    <ReviewGate>
      <MemberJourney key={revision} />
    </ReviewGate>
  );
}
function MemberJourney() {
  const { publicDecision, decisionRecorded, reconsideration, requestReview } =
    useAIReview();
  const [manual, setManual] = useState(false);
  const [documents, setDocuments] = useState<string[]>([]);
  const [provider, setProvider] = useState("");
  const [amount, setAmount] = useState("");
  const [intakeDone, setIntakeDone] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [requestOpen, setRequestOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [support, setSupport] = useState<string[]>([]);
  const [requestError, setRequestError] = useState("");
  const outcome = publicDecision.outcome;
  const canRequest =
    outcome === "Reject" ||
    (outcome === "Approve" && Number(publicDecision.amount) < 150);
  return (
    <div className="mx-auto max-w-5xl">
      <PreviewNotice />
      <BackToOversight />
      <h1 className="text-2xl font-semibold tracking-tight">
        Member claim journey
      </h1>
      <p className="mb-6 mt-2 text-sm text-muted-foreground">
        Try the member-facing copy and actions. This uses the sample assessor
        decision.
      </p>
      <Tabs defaultValue="intake">
        <TabsList aria-label="Member claim screens" className="mb-6">
          <TabsTrigger value="intake">New claim</TabsTrigger>
          <TabsTrigger value="outcome">Claim outcome</TabsTrigger>
        </TabsList>
        <TabsContent value="intake">
          <section
            className={`leaf ${panelClass} mx-auto max-w-xl p-6 sm:p-8 [&_button]:min-h-11`}
          >
            <p className="text-xs text-muted-foreground">
              Member A · Sample member
            </p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight">
              New claim
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">GP visit</p>
            {intakeDone ? (
              <div role="status" className="mt-8 space-y-5">
                <CheckCircle2 className="size-8 text-good" aria-hidden="true" />
                <h3 className="text-lg font-semibold">
                  Sample claim is ready for review
                </h3>
                <p className="text-sm text-muted-foreground">
                  Your details are complete in this preview. No claim was
                  submitted and no documents were uploaded.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIntakeDone(false)}
                >
                  Back to details
                </Button>
              </div>
            ) : (
              <>
                <div className="mt-7 rounded-lg border border-border bg-muted/40 p-5">
                  <h3 className="font-semibold">
                    Read details from your documents
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    AI reads your documents to suggest claim details. Check and
                    edit the suggestions. A person makes the claim decision.
                  </p>
                  <label className="mt-5 block">
                    <span className="mb-2 block text-sm font-medium">
                      Supporting documents
                    </span>
                    <input
                      type="file"
                      multiple
                      accept=".pdf,.png,.jpg,.jpeg"
                      className={`${fieldClass} text-xs file:mr-3 file:rounded file:border-0 file:bg-muted file:px-3 file:py-1 file:text-foreground`}
                      onChange={(e) => {
                        setDocuments(
                          Array.from(e.target.files ?? [], (f) => f.name),
                        );
                      }}
                    />
                  </label>
                  <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                    Preview: only filenames are shown. No document content is
                    read, uploaded or sent to AI.
                  </p>
                  {documents.length > 0 && (
                    <ul className="mt-3 space-y-2">
                      {documents.map((name, i) => (
                        <li
                          key={`${name}-${i}`}
                          className="flex items-start gap-2 break-all text-xs"
                        >
                          <FileText
                            className="size-4 shrink-0"
                            aria-hidden="true"
                          />
                          {name}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setManual(false);
                        setProvider("GP Clinic");
                        setAmount("150.00");
                      }}
                    >
                      Try sample autofill
                    </Button>
                    <Button
                      type="button"
                      variant="link"
                      onClick={() => setManual(true)}
                    >
                      Enter details myself
                    </Button>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                    Manual entry skips autofill. Submitted documents may still
                    be checked with AI.
                  </p>
                  <button
                    type="button"
                    className="mt-2 min-h-11 text-left text-xs text-foreground underline underline-offset-4"
                    aria-expanded={privacyOpen}
                    onClick={() => setPrivacyOpen(!privacyOpen)}
                  >
                    How your information is used
                  </button>
                  {privacyOpen && (
                    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                      Documents support your claim assessment. Authorized claims
                      staff review them, with AI assistance where configured.
                      The production flow will link to your company’s approved
                      privacy information.
                    </p>
                  )}
                </div>
                <form
                  className="mt-6 space-y-5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (provider.trim() && Number(amount) > 0)
                      setIntakeDone(true);
                  }}
                >
                  <p
                    role="status"
                    className="text-xs font-medium text-muted-foreground"
                  >
                    {manual
                      ? "Manual entry selected. Add the details below."
                      : provider
                        ? "Sample suggestions added. Check and edit them before continuing."
                        : "Enter your details, or try the sample suggestions."}
                  </p>
                  <Field label="Clinic or provider">
                    <Input
                      required
                      value={provider}
                      onChange={(e) => setProvider(e.target.value)}
                    />
                  </Field>
                  <Field label="Amount claimed (SGD)">
                    <Input
                      required
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                    />
                  </Field>
                  <Button type="submit" className="w-full">
                    Continue
                    <ChevronRight className="size-4" aria-hidden="true" />
                  </Button>
                </form>
              </>
            )}
          </section>
        </TabsContent>
        <TabsContent value="outcome">
          <section
            className={`leaf ${panelClass} mx-auto max-w-xl p-6 sm:p-8 [&_button]:min-h-11`}
          >
            <p className="text-xs text-muted-foreground">
              Member A · Sample member
            </p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight">
              Claim details
            </h2>
            <p className="mt-2 text-xs text-muted-foreground">
              DEMO-1042 · GP visit
            </p>
            {!decisionRecorded && (
              <p className="mt-4 text-xs text-muted-foreground">
                Showing the initial sample outcome. Record a decision in the
                assessor screen to update it.
              </p>
            )}
            <div className="mt-6 rounded-lg bg-muted/50 p-5">
              <p className="text-sm font-medium">
                {outcome === "Approve"
                  ? "Approved"
                  : outcome === "Reject"
                    ? "Rejected"
                    : "More information needed"}
              </p>
              {outcome === "Approve" && (
                <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">
                  S${Number(publicDecision.amount).toFixed(2)}
                </p>
              )}
              <dl className="mt-5 space-y-3 text-sm">
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Amount claimed</dt>
                  <dd>S$150.00</dd>
                </div>
                {outcome === "Approve" && (
                  <div className="flex justify-between">
                    <dt className="text-muted-foreground">Difference</dt>
                    <dd>
                      S${(150 - Number(publicDecision.amount)).toFixed(2)}
                    </dd>
                  </div>
                )}
              </dl>
              <div className="mt-5 border-t border-border pt-4">
                <h3 className="text-sm font-semibold">
                  {outcome === "Approve"
                    ? "Why this amount was approved"
                    : "Explanation from the claims team"}
                </h3>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed">
                  {publicDecision.explanation ||
                    "Your claim was approved in full."}
                </p>
              </div>
            </div>
            {reconsideration ? (
              <section role="status" className="mt-6 space-y-4">
                <Status value="Review requested" />
                <p className="text-sm leading-relaxed text-muted-foreground">
                  The current claim decision remains on record while the claims
                  team reviews your request.
                </p>
                <p className="whitespace-pre-wrap break-words rounded-md border border-border p-4 text-sm">
                  {reconsideration}
                </p>
                <p className="text-xs text-muted-foreground">
                  Sample request only · No message was sent.
                </p>
              </section>
            ) : (
              canRequest && (
                <section className="mt-6">
                  <h3 className="font-semibold">Need us to look again?</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    You can explain what you would like the claims team to
                    reconsider.
                  </p>
                  {!requestOpen ? (
                    <Button
                      type="button"
                      variant="outline"
                      className="mt-5 w-full"
                      onClick={() => setRequestOpen(true)}
                    >
                      <MessageSquare className="size-4" aria-hidden="true" />
                      Request another review
                    </Button>
                  ) : (
                    <form
                      className="mt-5 space-y-4"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (!message.trim()) {
                          setRequestError(
                            "Tell us what you would like reconsidered.",
                          );
                          return;
                        }
                        requestReview(message.trim());
                        setRequestError("");
                      }}
                    >
                      <Field label="What would you like us to reconsider?">
                        <textarea
                          className={fieldClass}
                          rows={4}
                          value={message}
                          onChange={(e) => setMessage(e.target.value)}
                          aria-invalid={Boolean(requestError)}
                          aria-describedby={
                            requestError ? "request-error" : undefined
                          }
                        />
                      </Field>
                      {requestError && (
                        <p
                          id="request-error"
                          role="alert"
                          className="text-sm text-error"
                        >
                          {requestError}
                        </p>
                      )}
                      <Field
                        label="Supporting documents (optional)"
                        hint="Only filenames are shown in this preview."
                      >
                        <input
                          className={fieldClass}
                          type="file"
                          multiple
                          accept=".pdf,.png,.jpg,.jpeg"
                          onChange={(e) =>
                            setSupport(
                              Array.from(e.target.files ?? [], (f) => f.name),
                            )
                          }
                        />
                      </Field>
                      {support.map((name, i) => (
                        <p
                          key={i}
                          className="break-all text-xs text-muted-foreground"
                        >
                          <Upload
                            className="mr-1 inline size-3"
                            aria-hidden="true"
                          />
                          {name}
                        </p>
                      ))}
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        Submitting a request keeps the current decision on
                        record. It does not cancel or reverse any payment.
                      </p>
                      <div className="flex flex-wrap gap-3">
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => setRequestOpen(false)}
                        >
                          Cancel
                        </Button>
                        <Button type="submit">Submit review request</Button>
                      </div>
                    </form>
                  )}
                </section>
              )
            )}
            {outcome === "Request information" && (
              <p className="mt-6 text-sm leading-relaxed text-muted-foreground">
                Provide the information requested by the claims team through the
                existing claim conversation.
              </p>
            )}
          </section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
