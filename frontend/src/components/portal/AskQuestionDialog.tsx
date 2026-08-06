/** "Ask a question" — the only way a member starts a conversation.
 *
 * Until this existed the sole composer in the portal was the reply box on a
 * claim already submitted, so a question about coverage, family or a clinic had
 * nowhere to go.
 *
 * **It is a dialog on the Messages page, not a page of its own.** Asking is a
 * short, focused, abandonable task whose whole context — the conversations the
 * member already holds, one of which may be the answer — is the page behind it.
 * As a route it cost a navigation each way and left the member looking at an
 * empty screen to decide something they had just been looking at the answer to.
 *
 * **The claim option ROUTES; it does not create.** Picking "About a claim I've
 * sent" and then a claim opens THAT claim's own thread. A new thread tagged to a
 * claim would be two conversations about one thing, each readable while the
 * other still shows unread — the second-reading-surface trap the Messages page
 * already refuses. Routing also means the member frequently gets their answer
 * before asking: a claim sitting at "More info needed" says what is missing the
 * moment they land on it.
 *
 * The vocabulary is SERVED, including which option routes. A hardcoded list
 * here would be a second place for it to change.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { usePortalClaims } from "@/api/portal";
import { useCreateEnquiry, useEnquiryTopics } from "@/api/portalEnquiries";
import { Action } from "@/components/portal/leaf/Action";
import { claimTitle } from "@/components/portal/leaf/ClaimMount";
import { formatDay } from "@/components/portal/leaf/date";
import { currencySymbol, moneyText } from "@/components/portal/leaf/Figure";
import { LeafDialog } from "@/components/portal/leaf/LeafDialog";
import { MountRule } from "@/components/portal/leaf/Mount";
import { useCompany } from "@/components/portal/useCompany";
import { cn } from "@/lib/cn";
import { formatError } from "@/lib/errors";

const MAX_SUBJECT = 255;
const MAX_BODY = 2000;

const fieldClass =
  "leaf-focus w-full rounded-control border border-leaf-input bg-bar/80 " +
  "px-3 py-2.5 text-row text-record placeholder:text-label";

/** A pickable row. Native inputs throughout — the member surface has no Radix
 * portal to escape `.leaf`, which is why its controls are native everywhere,
 * and why this dialog is a native `<dialog>` (see `LeafDialog`).
 *
 * The whole row is the target and it clears 44px on touch, per the Reach Rule.
 */
function PickRow({
  checked,
  onPick,
  title,
}: {
  checked: boolean;
  onPick: () => void;
  title: string;
}) {
  return (
    <label
      className={cn(
        "flex min-h-11 cursor-pointer items-center gap-3 rounded-control px-3 py-2.5",
        "transition-colors duration-200 ease-leaf",
        checked ? "bg-shade" : "hover:bg-shade/60",
      )}
    >
      <input
        type="radio"
        name="ask-topic"
        checked={checked}
        onChange={onPick}
        className="size-4 shrink-0 accent-action-ink"
      />
      <span className="min-w-0 text-row font-medium text-record">{title}</span>
    </label>
  );
}

/** A claim row, in the routing list and nowhere else. */
function ClaimPick({
  title,
  detail,
  onPick,
}: {
  title: string;
  detail: string;
  onPick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        className={cn(
          "leaf-focus block w-full rounded-control px-3 py-3 text-left",
          "transition-colors duration-200 ease-leaf hover:bg-shade/60",
        )}
      >
        <span className="block text-row font-medium text-record">{title}</span>
        <span className="mt-0.5 block text-row text-label">{detail}</span>
      </button>
    </li>
  );
}

export function AskQuestionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  /** Where the new thread should be READ. The page owns this because only it
   *  knows whether it is showing a stage: below the stage width the `?open=`
   *  param is inert, so navigating there landed the member back on the list
   *  they had just asked from, with no sign of what they had sent. */
  onCreated: (enquiryId: string) => void;
}) {
  const navigate = useNavigate();
  const company = useCompany();
  const topics = useEnquiryTopics();
  const claims = usePortalClaims();
  const create = useCreateEnquiry();

  const [topic, setTopic] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [aboutClaimId, setAboutClaimId] = useState<string | null>(null);

  // A dismissed dialog is an abandoned draft. Reopening to find the previous
  // attempt still in the boxes reads as a message that failed to send.
  useEffect(() => {
    if (open) return;
    setTopic(null);
    setSubject("");
    setBody("");
    setAboutClaimId(null);
  }, [open]);

  const options = topics.data ?? [];
  const picked = options.find((t) => t.key === topic) ?? null;
  const routing = picked?.routes_to_claim === true;
  // A claim question is answered on the claim, so a DRAFT is excluded — nothing
  // has been sent, so there is nobody at the other end to write to.
  const claimRows = (claims.data?.items ?? []).filter((c) => c.status !== "draft");

  const openClaim = (claimId: string) => {
    onClose();
    void navigate({
      to: "/portal/$company/claims/$claimId",
      params: { company, claimId },
    });
  };

  const send = async () => {
    if (!topic || !subject.trim() || !body.trim()) return;
    try {
      const created = await create.mutateAsync({
        topic,
        subject: subject.trim(),
        body: body.trim(),
        about_claim_id: aboutClaimId,
      });
      onClose();
      // Straight to the new thread — as its own screen on a phone, or on the
      // stage beside the index it just joined at width. The page decides.
      onCreated(created.id);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <LeafDialog
      open={open}
      onClose={onClose}
      title="Ask a question"
      gloss="We'll answer in your messages."
    >
      {topics.isLoading ? (
        <p className="text-row text-label">Loading&hellip;</p>
      ) : topics.isError ? (
        <p className="text-row text-label">
          We couldn&rsquo;t load the options just now. Close this and try again.
        </p>
      ) : (
        <div className="space-y-4">
          <fieldset className="space-y-1">
            <legend className="leaf-label mb-1">What&rsquo;s it about?</legend>
            <div className="-mx-1">
              {options.map((t) => (
                <PickRow
                  key={t.key}
                  checked={topic === t.key}
                  onPick={() => {
                    setTopic(t.key);
                    setAboutClaimId(null);
                  }}
                  title={t.label}
                />
              ))}
            </div>
          </fieldset>

          {routing && (
            <div className="space-y-2">
              <MountRule />
              <p className="text-row text-label">
                We&rsquo;ll take you to that claim, where the whole conversation
                about it lives.
              </p>
              {claims.isLoading ? (
                <p className="text-row text-label">Loading&hellip;</p>
              ) : claimRows.length === 0 ? (
                <p className="text-row text-label">
                  You haven&rsquo;t sent a claim yet. Pick another option above
                  and we&rsquo;ll answer here.
                </p>
              ) : (
                <ul className="-mx-1 divide-y divide-hairline/75">
                  {claimRows.map((c) => (
                    <ClaimPick
                      key={c.id}
                      title={claimTitle(c)}
                      detail={`${formatDay(c.incurred_date)} · ${currencySymbol(
                        c.currency,
                      )}${moneyText(c.amount_claimed)}`}
                      onPick={() => openClaim(c.id)}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}

          {picked && !routing && (
            <div className="space-y-4">
              <MountRule />
              <div className="space-y-2">
                <label htmlFor="q-subject" className="leaf-label">
                  Subject
                </label>
                <input
                  id="q-subject"
                  value={subject}
                  maxLength={MAX_SUBJECT}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="In a few words"
                  className={fieldClass}
                />
              </div>
              <div className="space-y-2">
                <label htmlFor="q-body" className="leaf-label">
                  What would you like to know?
                </label>
                <textarea
                  id="q-body"
                  rows={4}
                  value={body}
                  maxLength={MAX_BODY}
                  onChange={(e) => setBody(e.target.value)}
                  placeholder="Tell us as much as you like — it all helps."
                  className={fieldClass}
                />
              </div>

              {/* Optional CONTEXT. A question that names a claim without
                  belonging to one — "why was my June one settled at less than I
                  paid?" — is the case routing cannot serve. Empty by default
                  and last, because most questions have nothing to do with a
                  claim. */}
              {claimRows.length > 0 && (
                <div className="space-y-2">
                  <label htmlFor="q-claim" className="leaf-label">
                    Related claim (optional)
                  </label>
                  <select
                    id="q-claim"
                    value={aboutClaimId ?? ""}
                    onChange={(e) => setAboutClaimId(e.target.value || null)}
                    className={fieldClass}
                  >
                    <option value="">Not about a particular claim</option>
                    {claimRows.map((c) => (
                      <option key={c.id} value={c.id}>
                        {claimTitle(c)} · {formatDay(c.incurred_date)}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <Action
                tone="primary"
                block
                disabled={!subject.trim() || !body.trim() || create.isPending}
                onClick={() => void send()}
              >
                {create.isPending ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Send className="size-4" aria-hidden />
                )}
                Send
              </Action>
            </div>
          )}
        </div>
      )}
    </LeafDialog>
  );
}
