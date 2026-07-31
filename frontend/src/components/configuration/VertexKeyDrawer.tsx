import { useEffect, useRef, useState } from "react";
import { Loader2, Lock, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { FieldLabel, InfoHint } from "@/components/ui/tooltip";

// Vertex/Gemini is the sole provider (AWS Bedrock + Anthropic were removed).
// The location is FIXED, not a default: claim documents are Singapore-resident
// and asia-southeast1 is Google's only Singapore region, so the backend refuses
// every other value on save (core/ai_config.py::assert_vertex_location_writable).
// Offering a free-text box here only let an operator compose a request the
// server is guaranteed to reject.
export const VERTEX_LOCATION = "asia-southeast1";
export const DEFAULT_VERTEX_MODEL = "gemini-3.5-flash";

export interface VertexKeyDraft {
  location: string;
  model: string;
  serviceAccountJson: string;
}

export const EMPTY_VERTEX_DRAFT: VertexKeyDraft = {
  location: VERTEX_LOCATION,
  model: "",
  serviceAccountJson: "",
};

/** A Vertex key is the service-account JSON file — sanity-check the markers so
 *  we don't send an obviously-wrong value (an API key, a truncated paste). */
export function isValidServiceAccountJson(value: string): boolean {
  try {
    const data = JSON.parse(value) as Record<string, unknown>;
    return (
      data.type === "service_account" &&
      typeof data.private_key === "string" &&
      typeof data.client_email === "string" &&
      typeof data.project_id === "string" &&
      data.project_id.length > 0
    );
  } catch {
    return false;
  }
}

export function isValidVertexDraft(draft: VertexKeyDraft): boolean {
  if (!draft.location.trim()) return false;
  return isValidServiceAccountJson(draft.serviceAccountJson);
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** Extra context line above the fields (who this key applies to). */
  scopeNote: React.ReactNode;
  /** Location/model of the stored key, seeded into the draft when opening. */
  initial: { location?: string | null; model?: string | null } | null;
  /** Masked stored key, used only as the textarea placeholder. */
  storedKeyMasked?: string | null;
  saving: boolean;
  testing: boolean;
  onSave: (draft: VertexKeyDraft) => void;
  onTest: (draft: VertexKeyDraft) => void;
}

/**
 * The Vertex credential editor, shared by the platform key (system-admin) and
 * per-company BYOK (broker-admin). Both store the same three values, so they
 * must validate and look identical — hence one component, not two.
 */
export function VertexKeyDrawer({
  open,
  onOpenChange,
  title,
  scopeNote,
  initial,
  storedKeyMasked,
  saving,
  testing,
  onSave,
  onTest,
}: Props) {
  const [draft, setDraft] = useState<VertexKeyDraft>(EMPTY_VERTEX_DRAFT);
  const seededForOpen = useRef(false);

  // Seed ONCE per open transition: the stored key is never returned to the
  // browser, so only location/model carry over and the paste box starts empty.
  //
  // The ref is load-bearing. The Configure button renders while the config
  // query is still loading, so `initial` can arrive AFTER the drawer is open —
  // re-running on that would reset `serviceAccountJson` and silently wipe a
  // key the user had already pasted.
  useEffect(() => {
    if (!open) {
      seededForOpen.current = false;
      return;
    }
    if (seededForOpen.current) return;
    seededForOpen.current = true;
    setDraft({
      // Always the fixed region — NOT `initial.location`. A row written before
      // the region was locked could carry a stale non-Singapore value, and
      // seeding that would round-trip it straight back into a 400 on save.
      location: VERTEX_LOCATION,
      model: initial?.model ?? "",
      serviceAccountJson: "",
    });
  }, [open, initial?.model]);

  const keyTouched = draft.serviceAccountJson.trim() !== "";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-1.5">
            {title}
            <InfoHint>
              The service-account key is encrypted at rest. Other fields are
              stored in plain text.
            </InfoHint>
          </SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-4">
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
            {scopeNote}
          </div>
          <div className="flex flex-col gap-1.5">
            <FieldLabel hint="Claim documents are Singapore-resident, and asia-southeast1 is Google's only Singapore region — so this is fixed, not a choice. The backend rejects any other value on save.">
              GCP location
            </FieldLabel>
            <div className="flex h-9 items-center gap-2 rounded-md border border-input bg-muted px-3 text-sm">
              <Lock className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="font-mono">{VERTEX_LOCATION}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                Singapore
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <FieldLabel
              hint={
                <>
                  The Gemini model id. Leave blank to use{" "}
                  <code>{DEFAULT_VERTEX_MODEL}</code>.
                </>
              }
            >
              Gemini model (optional)
            </FieldLabel>
            <Input
              value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              placeholder={DEFAULT_VERTEX_MODEL}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <FieldLabel hint="The full service-account JSON key file for a service account with the Vertex AI User role. Encrypted at rest; the project id is read from it. Never returned to the browser.">
              Service account JSON key
            </FieldLabel>
            <textarea
              value={draft.serviceAccountJson}
              onChange={(e) =>
                setDraft({ ...draft, serviceAccountJson: e.target.value })
              }
              placeholder={
                storedKeyMasked
                  ? `Stored: ${storedKeyMasked} — paste a new key file to replace`
                  : '{ "type": "service_account", "project_id": "inspro-ai", … }'
              }
              rows={6}
              autoComplete="off"
              spellCheck={false}
              className="flex w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-xs text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:border-ring"
            />
            {keyTouched && !isValidServiceAccountJson(draft.serviceAccountJson) && (
              <p className="text-xs text-error">
                This must be the full service-account JSON key (with{" "}
                <code>type</code>, <code>project_id</code>,{" "}
                <code>private_key</code> and <code>client_email</code>).
              </p>
            )}
          </div>
          <Button
            variant="outline"
            onClick={() => onTest(draft)}
            disabled={testing || !keyTouched}
            title={
              keyTouched
                ? undefined
                : storedKeyMasked
                  ? "Re-paste the key to test — the stored key is never returned to the browser"
                  : "Paste a service-account key to test"
            }
            className="w-full justify-center"
          >
            {testing ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Wand2 className="size-3.5" />
            )}
            Test connection (uses ~1 token)
          </Button>
        </SheetBody>
        <SheetFooter>
          <SheetClose asChild>
            <Button variant="outline">Cancel</Button>
          </SheetClose>
          <Button
            onClick={() => onSave(draft)}
            disabled={saving || !isValidVertexDraft(draft)}
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            Save
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
