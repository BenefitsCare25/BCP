import { usePortalTranslation } from "@/i18n/portal";
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
  Camera,
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
  const pt = usePortalTranslation();
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
      {pt(label)}
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
  const pt = usePortalTranslation();
  const input = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
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
                  ? pt("{0} document{1} uploaded", [autofillDocs.length, autofillDocs.length === 1 ? "" : "s"])
                  : pt("Autofill from your documents")}
              </span>
            </Action>
            <Hint label={pt("How to get the best autofill")}>
              {pt("Upload the full document set for this claim together (up to")}{" "}
              {MAX_AUTOFILL_FILES}  {pt("files) — for example a tax invoice, itemised bill and discharge summary. Keep every page of a document in one file. You can edit everything before submitting.")} </Hint>
            <input ref={camera} type="file" accept="image/jpeg,image/png" capture="environment" className="hidden" onChange={(event) => {
              const photo = event.target.files?.[0];
              event.target.value = "";
              if (photo) void form.runAutofill([photo]);
            }} />
            <Action type="button" disabled={form.extractIntake.isPending} onClick={() => camera.current?.click()} aria-label={pt("Photograph a receipt")}>
              <Camera className="size-4" aria-hidden /><span>{pt("Take photo")}</span>
            </Action>
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
                    {detectedType ? ` · ${pt(detectedType)}` : ""}
                    {destination ? ` → ${pt(destination)}` : ""}
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
            <p>{autofillNote.map(note => pt(note)).join(" ")}</p>
            {lowConfidence.length > 0 && (
              <p className="text-label">
                {pt("Double-check the")}{" "}
                {lowConfidence.map((k) => pt(LOW_CONF_LABELS[k] ?? k)).join(", ")}.
              </p>
            )}
          </div>
        </div>
      )}

      {form.intakeBaseline && <details className="rounded-control bg-bar/50 p-3 text-row">
        <summary className="cursor-pointer font-medium">{pt("Review document readings and your changes")}</summary>
        <p className="mt-2 text-label">{pt("Confidence is the model's reading estimate. Check the original document; it does not confirm eligibility or accuracy.")}</p>
        <dl className="mt-3 space-y-3">
          {Object.entries(form.intakeBaseline).filter(([, value]) => value != null).map(([field, original]) => {
            const current = form.intakeCurrent[field];
            const changed = String(original).trim() !== String(current ?? "").trim();
            const sources = form.fieldSources[field] ?? [];
            return <div key={field}>
              <dt className="font-medium">{pt(LOW_CONF_LABELS[field] ?? field)}{changed ? pt(" · Changed") : ""}</dt>
              <dd className="break-words">{pt("Read:")} {String(original)}{changed && <>  {pt("→ Now:")} {current ? String(current) : pt("Not used")}</>}</dd>
              <dd className="text-label">{sources.length ? sources.map((source) => `${source.file_name} (${pt("upload {0}", [source.upload_index + 1])}) · ${pt(source.source_label)} · ${source.confidence == null ? pt("Confidence not reported") : pt("{0}% model confidence", [Math.round(source.confidence * 100)])}`).join("; ") : pt("Derived suggestion; no direct source reading identified.")}</dd>
            </div>;
          })}
        </dl>
      </details>}
      <MountRule />
    </div>
  );
}
