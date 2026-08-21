/** The claim's evidence: one labelled upload per required slot, then anything
 * else the member wants the broker to see.
 *
 * The slots follow the claim type (and, for hospitalisation, the hospital's
 * sector), and submit blocks until each is filled — the same set the backend
 * enforces, so the form can never let through a claim it will reject.
 *
 * Autofill files that were not placed into a slot are listed under Additional
 * documents and are removable: nothing rides along invisibly. */
import { useRef } from "react";
import { CheckCircle2, Circle, Paperclip, X } from "lucide-react";
import { toast } from "sonner";
import { FieldGroup } from "@/components/portal/leaf/Field";
import { Action } from "@/components/portal/leaf/Action";
import { ACCEPT, MAX_BYTES } from "./claimForm";
import type { NewClaimForm } from "./useNewClaimForm";

const ATTACH_CHIP =
  "leaf-focus inline-flex min-h-11 max-w-full cursor-pointer items-center gap-1.5 " +
  "rounded-control border border-leaf-input bg-bar/80 px-3 text-row font-medium text-record " +
  "transition-colors duration-200 ease-leaf hover:bg-bar";

function RemoveButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="leaf-focus -m-3 inline-flex size-11 shrink-0 items-center justify-center text-label"
    >
      <X className="size-4" aria-hidden />
    </button>
  );
}

export function DocumentFields({ form }: { form: NewClaimForm }) {
  const extraInput = useRef<HTMLInputElement>(null);
  const { docSlots, slotFiles, files, unplacedAutofill } = form;
  const attachedCount = docSlots.filter((slot) => slotFiles[slot.key]).length;

  return (
    <>
      {docSlots.length > 0 && (
        <FieldGroup
          label="Documents for this claim"
          hint="Attach every document below before sending your claim."
          className="space-y-3"
        >
          <div
            className="flex items-center justify-between gap-3 text-row"
            aria-live="polite"
          >
            <span className="font-medium text-record">
              {attachedCount === docSlots.length ? "All documents attached" : "Required uploads"}
            </span>
            <span className="tabular-nums text-label">
              {attachedCount} of {docSlots.length} attached
            </span>
          </div>
          <div className="divide-y divide-hairline overflow-hidden rounded-control border border-leaf-input bg-bar/55">
          {docSlots.map((slot) => {
            const error = form.fieldErrors[`slot_${slot.key}`];
            const file = slotFiles[slot.key];
            return (
              <div
                key={slot.key}
                className="space-y-2 px-3 py-3"
                data-field-error={error ? "true" : undefined}
              >
                <div className="flex items-start gap-2.5">
                  {file ? (
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-strike-approved" aria-hidden />
                  ) : (
                    <Circle className="mt-0.5 size-4 shrink-0 text-label" aria-hidden />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-row font-semibold text-record">{slot.label}</p>
                    {slot.instructions && (
                      <p className="mt-0.5 text-row text-label">{slot.instructions}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3 pl-7">
                  <label className={ATTACH_CHIP}>
                    <Paperclip className="size-4 shrink-0" aria-hidden />
                    <span className="truncate">
                      {file?.name ?? "Attach PDF or photo"}
                    </span>
                    <input
                      type="file"
                      accept={ACCEPT}
                      className="sr-only"
                      aria-label={`${file ? "Replace" : "Attach"} ${slot.label}`}
                      aria-invalid={Boolean(error)}
                      onChange={(e) => {
                        const f = e.target.files?.[0] ?? null;
                        e.target.value = "";
                        if (!f) return;
                        if (f.size > MAX_BYTES) {
                          toast.error(`${f.name} exceeds 15 MB`);
                          return;
                        }
                        form.setSlotFile(slot.key, f);
                      }}
                    />
                  </label>
                  {file && (
                    <RemoveButton
                      label={`Remove the ${slot.label.toLowerCase()}`}
                      onClick={() => form.removeSlotFile(slot.key)}
                    />
                  )}
                </div>
                {error && (
                  <p role="alert" className="text-row font-medium text-strike-rejected">
                    {error}
                  </p>
                )}
              </div>
            );
          })}
          </div>
        </FieldGroup>
      )}

      {form.effectiveKind && (
        <FieldGroup label="Additional documents (optional)">
          <input
            ref={extraInput}
            type="file"
            accept={ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => {
              form.pickFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <Action type="button" onClick={() => extraInput.current?.click()}>
            <Paperclip className="size-4 shrink-0" aria-hidden />
            Attach document (PDF or photo)
          </Action>
          {(files.length > 0 || unplacedAutofill.length > 0) && (
            <ul className="space-y-1 pt-1">
              {unplacedAutofill.map((f, i) => (
                <li
                  key={`autofill-${f.name}-${i}`}
                  className="flex items-center justify-between gap-2 rounded-control bg-bar/70 px-3 py-1.5 text-row"
                >
                  <span className="min-w-0 truncate text-record">
                    {f.name} <span className="text-label">(from autofill)</span>
                  </span>
                  <RemoveButton
                    label={`Remove ${f.name}`}
                    onClick={() => form.dropAutofillFile(f)}
                  />
                </li>
              ))}
              {files.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center justify-between gap-2 rounded-control bg-bar/70 px-3 py-1.5 text-row"
                >
                  <span className="min-w-0 truncate text-record">{f.name}</span>
                  <RemoveButton
                    label={`Remove ${f.name}`}
                    onClick={() =>
                      form.setFiles((prev) => prev.filter((_, j) => j !== i))
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </FieldGroup>
      )}
    </>
  );
}
