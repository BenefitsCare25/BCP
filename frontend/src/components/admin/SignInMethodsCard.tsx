import { useId, useState } from "react";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { toast } from "sonner";
import type { SignInMethods, SignInMethodsUpdate } from "@/api/platform";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { ListError, ListLoading } from "@/components/platform/QueryStates";
import { formatError } from "@/lib/errors";

const DIRECTORY_ID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

type Draft = { entraOn: boolean; tenantId: string; requireMfa: boolean; localOn: boolean };

function draftOf(saved: SignInMethods): Draft {
  return {
    entraOn: saved.entra.enabled,
    tenantId: saved.entra.tenant_id ?? "",
    requireMfa: saved.entra.require_platform_mfa,
    localOn: saved.local.enabled,
  };
}

function directoryError(draft: Draft): string | null {
  const value = draft.tenantId.trim();
  if (!value) return draft.entraOn ? "Enter your Microsoft Entra directory (tenant) ID." : null;
  return DIRECTORY_ID.test(value)
    ? null
    : "The directory ID is a GUID, such as 00000000-0000-0000-0000-000000000000.";
}

/** How a firm's staff sign in: Microsoft 365 with the firm's own Entra
 *  directory, email and password (always with two-factor), or both. Shared by
 *  the firm console and the platform console, which bind it to their own
 *  endpoints. At least one method must stay on; the form refuses switching
 *  off the last one, and the server refuses it too. */
export function SignInMethodsCard({
  query,
  update,
  subject,
}: {
  query: UseQueryResult<SignInMethods>;
  update: UseMutationResult<SignInMethods, Error, SignInMethodsUpdate>;
  /** Whose staff, in the description: "your firm's staff", "Acme's staff". */
  subject: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Sign-in methods</CardTitle>
        <CardDescription>How {subject} sign in to the staff site.</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <ListLoading label="Loading sign-in methods…" />
        ) : query.isError ? (
          <ListError what="sign-in methods" error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          // Remounts from the saved values after every save or refetch.
          <SignInMethodsForm key={JSON.stringify(query.data)} saved={query.data} update={update} />
        )}
      </CardContent>
    </Card>
  );
}

function SignInMethodsForm({
  saved,
  update,
}: {
  saved: SignInMethods;
  update: UseMutationResult<SignInMethods, Error, SignInMethodsUpdate>;
}) {
  const id = useId();
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [refusal, setRefusal] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const initial = draftOf(saved);
  const dirty =
    draft.entraOn !== initial.entraOn ||
    draft.tenantId.trim() !== initial.tenantId ||
    draft.requireMfa !== initial.requireMfa ||
    draft.localOn !== initial.localOn;
  const tenantError = directoryError(draft);
  // Only a firm not yet set up starts with nothing on; it can still be edited.
  const noneOn = !draft.entraOn && !draft.localOn;

  const change = (next: Partial<Draft>) => {
    const merged = { ...draft, ...next };
    if (!merged.entraOn && !merged.localOn && !noneOn) {
      setRefusal("Keep at least one sign-in method on, or no one could sign in. Turn the other method on first.");
      return;
    }
    setRefusal(null);
    if (update.isError) update.reset();
    setDraft(merged);
  };

  const save = () => {
    setShowErrors(true);
    if (tenantError || noneOn || update.isPending) return;
    update.mutate(
      {
        entra: {
          enabled: draft.entraOn,
          tenant_id: draft.tenantId.trim() || null,
          require_platform_mfa: draft.requireMfa,
        },
        local: { enabled: draft.localOn },
      },
      { onSuccess: () => toast.success("Sign-in methods saved") },
    );
  };

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <MicrosoftSection
        id={id}
        draft={draft}
        saved={saved}
        tenantError={showErrors || draft.tenantId.trim() ? tenantError : null}
        disabled={update.isPending}
        onChange={change}
      />
      <Separator />
      <ToggleRow
        id={`${id}-local`}
        label="Email and password"
        help="Staff sign in with their work email and a password they set from their invitation. Two-factor verification with an authenticator app is always required for this method."
        checked={draft.localOn}
        disabled={update.isPending}
        onChange={(on) => change({ localOn: on })}
      />
      {noneOn && (
        <p role="status" className="text-sm text-warn">
          No sign-in method is on, so no one can sign in. Turn one on and save.
        </p>
      )}
      {refusal && <p role="alert" className="text-sm text-error">{refusal}</p>}
      {update.isError && <p role="alert" className="text-sm text-error">{formatError(update.error)}</p>}
      <FormActions
        dirty={dirty}
        canSave={dirty && !noneOn}
        pending={update.isPending}
        onDiscard={() => {
          setDraft(initial);
          setRefusal(null);
          setShowErrors(false);
          update.reset();
        }}
      />
    </form>
  );
}

function FormActions({
  dirty,
  canSave,
  pending,
  onDiscard,
}: {
  dirty: boolean;
  canSave: boolean;
  pending: boolean;
  onDiscard: () => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="submit" loading={pending} disabled={!canSave || pending}>
        Save sign-in methods
      </Button>
      {dirty && (
        <Button type="button" variant="ghost" disabled={pending} onClick={onDiscard}>
          Discard changes
        </Button>
      )}
    </div>
  );
}

function ToggleRow({
  id,
  label,
  help,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <Label htmlFor={id} className="text-sm">
          {label}
        </Label>
        <p id={`${id}-help`} className="max-w-xl text-xs text-muted-foreground">{help}</p>
      </div>
      <Switch
        id={id}
        aria-describedby={`${id}-help`}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </div>
  );
}

function MicrosoftSection({
  id,
  draft,
  saved,
  tenantError,
  disabled,
  onChange,
}: {
  id: string;
  draft: Draft;
  saved: SignInMethods;
  tenantError: string | null;
  disabled: boolean;
  onChange: (next: Partial<Draft>) => void;
}) {
  const consentUrl = saved.admin_consent_url?.startsWith("https://") ? saved.admin_consent_url : null;
  const savedTenant = saved.entra.tenant_id ?? "";
  const consentCurrent = consentUrl !== null && draft.tenantId.trim() === savedTenant;
  return (
    <div className="space-y-4">
      <ToggleRow
        id={`${id}-entra`}
        label="Microsoft 365"
        help="Staff sign in with their work account from your organisation's Microsoft Entra directory."
        checked={draft.entraOn}
        disabled={disabled}
        onChange={(on) => onChange({ entraOn: on })}
      />
      {draft.entraOn && (
        <div className="space-y-4 rounded-md border border-border bg-muted/30 p-3">
          <div className="flex max-w-md flex-col gap-1.5">
            <Label htmlFor={`${id}-tenant`}>Directory (tenant) ID</Label>
            <Input
              id={`${id}-tenant`}
              value={draft.tenantId}
              spellCheck={false}
              autoComplete="off"
              placeholder="00000000-0000-0000-0000-000000000000"
              disabled={disabled}
              aria-invalid={tenantError ? true : undefined}
              aria-describedby={`${id}-tenant-help${tenantError ? ` ${id}-tenant-error` : ""}`}
              onChange={(event) => onChange({ tenantId: event.target.value })}
              className="font-mono"
            />
            <p id={`${id}-tenant-help`} className="text-xs text-muted-foreground">
              In the Microsoft Entra admin center, under Overview → Tenant ID.
            </p>
            {tenantError && (
              <p id={`${id}-tenant-error`} role="alert" className="text-xs text-error">{tenantError}</p>
            )}
          </div>
          <ToggleRow
            id={`${id}-platform-mfa`}
            label="Also require the Inspro authenticator"
            help="After Microsoft sign-in, staff also enter a code from an authenticator app, in addition to any verification Microsoft asks for."
            checked={draft.requireMfa}
            disabled={disabled}
            onChange={(on) => onChange({ requireMfa: on })}
          />
          <div className="space-y-2">
            <p className="text-sm font-medium text-foreground">Administrator consent</p>
            <p className="max-w-xl text-xs text-muted-foreground">
              Your organisation&apos;s Microsoft 365 administrator approves Inspro once, on
              Microsoft&apos;s site, before staff can sign in. If that isn&apos;t you, send them this link.
            </p>
            {consentCurrent ? (
              <Button asChild size="sm" variant="outline">
                <a href={consentUrl} target="_blank" rel="noopener noreferrer">
                  Grant consent
                  <ExternalLink className="size-3.5" aria-hidden="true" />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              </Button>
            ) : (
              <p className="text-xs text-muted-foreground">
                {savedTenant && draft.tenantId.trim() === savedTenant
                  ? "A consent link isn't available for this directory yet."
                  : "Save the directory ID to get the consent link."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
