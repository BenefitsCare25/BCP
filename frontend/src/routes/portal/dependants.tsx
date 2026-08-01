/** "My family" — the people on the member's leaf, plus self-service addition.
 * Added family members are pending until HR approves them (optionally with a
 * proof document). */
import { useRef, useState } from "react";
import { Loader2, Paperclip, UserPlus, X } from "lucide-react";
import { toast } from "sonner";
import {
  useAddDependant,
  usePortalDependants,
  useUploadDependantProof,
} from "@/api/portal";
import { formatError, isNotFoundError } from "@/lib/errors";
import { DependantsLeaf } from "@/components/portal/leaf/DependantsLeaf";
import { Field, FormAlert, leafControl } from "@/components/portal/leaf/Field";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount } from "@/components/portal/leaf/Mount";
import { Action } from "@/components/portal/leaf/Action";
import { PortalErrorState } from "@/components/portal/PortalErrorState";

export function PortalDependantsPage() {
  const dependants = usePortalDependants();
  const addDependant = useAddDependant();
  const uploadProof = useUploadDependantProof();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [dob, setDob] = useState("");
  const [proof, setProof] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{
    name?: string;
    relationship?: string;
  }>({});
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const submit = async () => {
    setError(null);
    // Named per field rather than as one combined sentence, so the message sits
    // beside the control it is about and is announced with it.
    const next: { name?: string; relationship?: string } = {};
    if (!name.trim()) next.name = "Enter their full name.";
    if (!relationship.trim()) next.relationship = "Choose how they're related to you.";
    setFieldErrors(next);
    if (Object.keys(next).length > 0) return;

    setBusy(true);
    try {
      const created = await addDependant.mutateAsync({
        name: name.trim(),
        relationship: relationship.trim(),
        dob: dob || null,
      });
      if (proof) {
        await uploadProof.mutateAsync({ dependantId: created.id, file: proof });
      }
      toast.success("Sent to your HR team for approval");
      setShowForm(false);
      setName("");
      setRelationship("");
      setDob("");
      setProof(null);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  };

  if (dependants.isLoading)
    return <LeafSkeleton label="Loading your family" mounts={2} />;

  // A fetch failure must not read as "no dependants on record" — only a 404
  // falls through to the empty state.
  if (dependants.isError && !isNotFoundError(dependants.error)) {
    return <PortalErrorState onRetry={() => void dependants.refetch()} />;
  }

  const rows = dependants.data ?? [];

  return (
    <div className="space-y-3">
      {/* Quiet, not brand: the page's one brand fill is "Send for approval"
          inside the form, and adding a family member is the step BEFORE the
          member has anything to submit. Full width on a phone so it lands where
          every other member action does. */}
      {!showForm && (
        <div className="flex sm:justify-end">
          <Action block="phone" onClick={() => setShowForm(true)}>
            <UserPlus className="size-4" aria-hidden />
            Add a family member
          </Action>
        </div>
      )}

      {showForm && (
        <Mount
          label="Add a family member"
          gloss="Your HR team checks every addition before their cover starts. Attaching a birth or marriage certificate usually speeds that up."
          aside={
            <button
              type="button"
              // Disabled while a submission is in flight. Cancelling mid-submit
              // unmounts the form, and both the failure message and the file
              // upload's error live inside it — so a create that succeeded with
              // a failed proof upload left a pending dependant on file and told
              // the member nothing at all.
              disabled={busy}
              onClick={() => {
                setShowForm(false);
                setError(null);
                setFieldErrors({});
              }}
              aria-label="Cancel adding a family member"
              className="leaf-focus -mr-2 -mt-2 inline-flex size-11 items-center justify-center text-label disabled:opacity-50"
            >
              <X className="size-4" aria-hidden />
            </button>
          }
        >
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            {/* One column on a phone. A frame is either full width or it is not
                on this breakpoint — never a half-width field (The Whole-Frame
                Rule); the two-up grid used to hold a ~147px date input. */}
            <Field label="Full name" required error={fieldErrors.name}>
              {(p) => (
                <input
                  {...p}
                  className={leafControl}
                  value={name}
                  autoFocus
                  autoComplete="name"
                  onChange={(e) => setName(e.target.value)}
                />
              )}
            </Field>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="How they're related to you"
                required
                error={fieldErrors.relationship}
              >
                {(p) => (
                  <select
                    {...p}
                    className={leafControl}
                    value={relationship}
                    onChange={(e) => setRelationship(e.target.value)}
                  >
                    <option value="">Choose one…</option>
                    <option value="spouse">Husband or wife</option>
                    <option value="child">Child</option>
                  </select>
                )}
              </Field>

              <Field label="Date of birth" hint="If you know it.">
                {(p) => (
                  <input
                    {...p}
                    type="date"
                    className={leafControl}
                    value={dob}
                    onChange={(e) => setDob(e.target.value)}
                  />
                )}
              </Field>
            </div>

            <div>
              <input
                ref={fileInput}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                className="hidden"
                onChange={(e) => setProof(e.target.files?.[0] ?? null)}
              />
              <div className="flex flex-wrap items-center gap-2">
                <Action
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="max-w-full"
                >
                  <Paperclip className="size-4 shrink-0" aria-hidden />
                  <span className="truncate">
                    {proof ? proof.name : "Attach a certificate (optional)"}
                  </span>
                </Action>
                {proof && (
                  <button
                    type="button"
                    onClick={() => setProof(null)}
                    className="leaf-focus min-h-11 px-2 text-row text-label underline"
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>

            {error && <FormAlert>{error}</FormAlert>}

            {/* The page's one brand fill. */}
            <Action
              tone="primary"
              type="submit"
              disabled={busy}
              block="phone"
            >
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Send for approval
            </Action>
          </form>
        </Mount>
      )}

      {rows.length > 0 && <DependantsLeaf rows={rows} />}
      {rows.length === 0 && !showForm && <DependantsLeaf rows={[]} />}
    </div>
  );
}
