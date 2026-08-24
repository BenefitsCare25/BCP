/** Autofill from documents — the AI reads the upload(s) and prefills the form;
 * everything stays editable and the files become the claim's evidence.
 *
 * One row, not three. A heading would say what the button's own label already
 * says, and stacking heading + button + section padding spent ~130px before the
 * form began — on the shortcut, not the task. The how-to sits behind a TAP-open
 * hint: a phone has no hover state, so a tooltip's content cannot be reached
 * there at all.
 *
 * That row is also the form's header, so it takes a `leading` slot for the
 * route's own furniture (today: the back link). The slot exists rather than the
 * link being imported here because navigation belongs to the route — this
 * component would otherwise need to know where "back" goes. */
import type { ReactNode } from "react";
import { useRef } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Cloud,
  Loader2,
  Paperclip,
  Sparkles,
} from "lucide-react";
import { Hint } from "@/components/ui/hint";
import { Action } from "@/components/portal/leaf/Action";
import { MountRule } from "@/components/portal/leaf/Mount";
import {
  ACCEPT,
  LOW_CONF_LABELS,
  MAX_AUTOFILL_FILES,
} from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

function DraftStatus({ status }: { status: NewClaimForm["draftStatus"] }) {
  if (status === "idle") return null;
  const error = status === "error";
  const Icon = error ? AlertCircle : status === "saved" ? CheckCircle2 : Cloud;
  const label =
    status === "saving"
      ? "Saving draft…"
      : error
        ? "Draft not saved"
        : "Draft saved";

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 text-row font-medium ${
        error ? "text-strike-rejected" : "text-record"
      }`}
      aria-live="polite"
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      {label}
    </span>
  );
}

export function AutofillCard({
  form,
  leading,
}: {
  form: NewClaimForm;
  /** Rendered at the start of the header row, opposite the autofill control. */
  leading?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  const { autofillDocs, autofillNote, lowConfidence, docSlots, slotFiles } =
    form;

  return (
    <div className="space-y-2">
      {/* Stacked on a phone, opposed from `sm` up. Side by side at 390px the
          back link and a pill reading "Autofill from your documents" do not
          both fit, and shortening the pill's label to make them fit would cost
          the one thing that explains what the shortcut does. */}
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        {leading}
        <div className="flex min-w-0 flex-col items-start gap-2 max-sm:w-full sm:flex-row sm:items-center">
          <DraftStatus status={form.draftStatus} />
          <div className="flex min-w-0 items-center gap-1 max-sm:w-full">
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              multiple
              className="hidden"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (picked.length) void form.runAutofill(picked);
              }}
            />
            <Action
              type="button"
              className="min-w-0 flex-1 justify-start sm:flex-none"
              disabled={form.extractIntake.isPending}
              onClick={() => input.current?.click()}
            >
              {form.extractIntake.isPending ? (
                <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
              ) : (
                <Sparkles className="size-4 shrink-0" aria-hidden />
              )}
              <span className="truncate">
                {autofillDocs.length > 0
                  ? `${autofillDocs.length} document${autofillDocs.length === 1 ? "" : "s"} uploaded`
                  : "Autofill from your documents"}
              </span>
            </Action>
            <Hint label="How to get the best autofill">
              Upload the full document set for this claim together (up to{" "}
              {MAX_AUTOFILL_FILES} files) — for example a tax invoice, itemised
              bill and discharge summary. Keep every page of a document in one
              file. You can edit everything before submitting.
            </Hint>
          </div>
        </div>
      </div>

      {autofillDocs.length > 0 && (
        <ul className="space-y-1">
          {autofillDocs.map(({ file, detectedType }, i) => {
            // Where this file goes on submit: the required-document slot it
            // fills, else it rides along as an additional document.
            const filledSlot = docSlots.find((s) => slotFiles[s.key] === file);
            const destination = filledSlot
              ? filledSlot.label
              : form.effectiveKind
                ? "additional document"
                : null;
            return (
              <li
                key={`${file.name}-${i}`}
                className="flex items-center gap-1.5 text-row text-label"
              >
                <Paperclip className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">{file.name}</span>
                {(detectedType || destination) && (
                  <span className="shrink-0">
                    {detectedType ? ` · ${detectedType}` : ""}
                    {destination ? ` → ${destination}` : ""}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {autofillNote && (
        <div className="flex items-start gap-1.5 rounded-control bg-bar/70 px-3 py-2 text-row text-record">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-label" aria-hidden />
          <div className="space-y-1">
            <p>{autofillNote}</p>
            {lowConfidence.length > 0 && (
              <p className="text-label">
                Double-check the{" "}
                {lowConfidence.map((k) => LOW_CONF_LABELS[k] ?? k).join(", ")}.
              </p>
            )}
          </div>
        </div>
      )}

      <MountRule />
    </div>
  );
}
