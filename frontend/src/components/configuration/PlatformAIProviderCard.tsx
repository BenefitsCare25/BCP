import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  useDeletePlatformAICredentials,
  usePlatformAISettings,
  useSetPlatformAICredentials,
  useTestPlatformAICredentials,
} from "@/api/hooks";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DEFAULT_VERTEX_MODEL,
  saveVertexKeyAfterProbe,
  VertexKeyDrawer,
  type VertexKeyDraft,
} from "@/components/configuration/VertexKeyDrawer";
import { formatError } from "@/lib/errors";

/**
 * The PLATFORM AI key — the default every company runs on. System-admin only.
 *
 * This is the normal way AI gets configured: set it once here and every
 * company works. A company can still override it with its own BYOK key
 * (`/ai-config`, broker-admin), which is why the copy below names the order.
 */
export function PlatformAIProviderCard() {
  const { data, isLoading, isError, error, refetch } = usePlatformAISettings();
  const save = useSetPlatformAICredentials();
  const remove = useDeletePlatformAICredentials();
  const test = useTestPlatformAICredentials();

  const [open, setOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const creds = data?.credentials;
  const configured = creds?.configured ?? false;
  const loadFailed = !data && isError;

  const onSave = async (draft: VertexKeyDraft) => {
    const body = {
      location: draft.location.trim() || null,
      model: draft.model.trim() || null,
      capacity_mode: draft.capacityMode,
      service_account_json: draft.serviceAccountJson,
    };
    setSubmitting(true);
    const saved = await saveVertexKeyAfterProbe(
      {
        probeDraft: () => test.mutateAsync(body),
        save: () => save.mutateAsync(body),
        probeStored: () => test.mutateAsync(undefined),
      },
      configured
        ? "Platform AI key updated and validated"
        : "Platform AI key saved and validated",
    );
    setSubmitting(false);
    if (saved) setOpen(false);
  };

  const runTest = async (draft?: VertexKeyDraft) => {
    try {
      const result = await test.mutateAsync(
        draft
          ? {
              location: draft.location.trim() || null,
              model: draft.model.trim() || null,
              capacity_mode: draft.capacityMode,
              service_account_json: draft.serviceAccountJson || null,
            }
          : undefined,
      );
      if (result.ok) {
        toast.success(`Connection OK · ${result.latency_ms}ms`);
      } else {
        toast.error(result.error ?? "Test failed");
      }
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <>
      <VertexKeyDrawer
        open={open}
        onOpenChange={setOpen}
        title={configured ? "Update platform AI key" : "Configure platform AI key"}
        scopeNote={
          <>
            Provider:{" "}
            <span className="font-medium text-foreground">Google Vertex — Gemini</span>
            , Singapore (data-resident). This key applies to{" "}
            <span className="font-medium text-foreground">every company</span> that
            hasn't set its own.
          </>
        }
        initial={creds ?? null}
        storedKeyMasked={creds?.key_masked}
        saving={submitting}
        testing={test.isPending}
        onSave={onSave}
        onTest={(draft) => void runTest(draft)}
      />

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <KeyRound className="size-4 text-muted-foreground" />
                <CardTitle>Platform AI key</CardTitle>
              </div>
              <CardDescription className="mt-1.5">
                The default AI credentials for every company on the platform. A
                company can override this with its own key; otherwise all AI —
                claims review, rule suggestion, roster profiling — runs on this
                one. System-admin only.
              </CardDescription>
            </div>
            {!isLoading && (
              <KeyStatusBadge
                status={
                  loadFailed
                    ? "unknown"
                    : configured
                      ? creds?.validation_status ?? "unvalidated"
                      : "none"
                }
              />
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading…
            </div>
          ) : loadFailed ? (
            <div className="flex flex-col items-start gap-3 text-sm text-error">
              <span>
                Couldn&apos;t load the platform AI key. {formatError(error)}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void refetch()}
              >
                <RefreshCw className="size-4" /> Retry
              </Button>
            </div>
          ) : configured && creds ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Provider" value={creds.provider ?? "vertex"} />
              <Field
                label="Model"
                value={creds.model ?? DEFAULT_VERTEX_MODEL}
                muted={!creds.model}
                hint={!creds.model ? "(default)" : undefined}
              />
              <Field
                label="GCP location"
                value={creds.location ?? "—"}
                className="font-mono text-xs"
              />
              <Field
                label="Service account"
                value={creds.key_masked ?? "—"}
                hint={creds.key_fingerprint ? `fp ${creds.key_fingerprint}` : undefined}
              />
              <Field
                label="Last validated"
                value={
                  creds.last_validated_at
                    ? new Date(creds.last_validated_at).toLocaleString()
                    : "Never"
                }
                muted={!creds.last_validated_at}
                className="md:col-span-2"
              />
              {creds.last_validation_error && (
                <div className="md:col-span-2 rounded-md border border-error/40 bg-error-soft/40 p-3 text-sm text-error flex items-start gap-2">
                  <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                  <span>{creds.last_validation_error}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground p-5 text-center border border-dashed border-border rounded-md">
              No platform key set — AI features are off for every company that
              hasn't configured its own. Add a Vertex service-account JSON to
              turn AI on platform-wide.
            </div>
          )}

          <div className="flex flex-wrap items-center justify-end gap-2">
            {configured && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void runTest()}
                  disabled={test.isPending}
                >
                  {test.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="size-3.5" />
                  )}
                  Test stored key
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-error hover:text-error"
                  onClick={() => setConfirmClear(true)}
                  disabled={remove.isPending}
                >
                  <Trash2 className="size-3.5" /> Clear key
                </Button>
              </>
            )}
            <Button onClick={() => setOpen(true)} disabled={!data}>
              {configured ? (
                <>
                  <Pencil className="size-4" /> Update
                </>
              ) : (
                <>
                  <KeyRound className="size-4" /> Configure
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      <AlertDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear the platform AI key?"
        description={
          <>
            AI will stop working for every company that hasn't configured its own
            key — claims review, rule suggestion and roster profiling all fall
            closed. The encrypted key is deleted; the platform limits are kept.
            The audit log records the change. This cannot be undone.
          </>
        }
        confirmLabel="Clear platform key"
        loading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync();
            toast.success("Platform AI key cleared");
            setConfirmClear(false);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </>
  );
}

// Production uses a stored key only once a test of exactly its saved settings
// has passed (`validation_status` "active"), so a saved key is not "on" yet.
const KEY_STATUS: Record<
  string,
  { variant: "good" | "warn" | "error"; icon: JSX.Element; label: string }
> = {
  active: { variant: "good", icon: <ShieldCheck className="size-3" />, label: "Active" },
  invalid: {
    variant: "error",
    icon: <AlertTriangle className="size-3" />,
    label: "Validation failed",
  },
  unvalidated: {
    variant: "warn",
    icon: <AlertTriangle className="size-3" />,
    label: "Not validated",
  },
  none: { variant: "warn", icon: <AlertTriangle className="size-3" />, label: "Not configured" },
  unknown: {
    variant: "warn",
    icon: <AlertTriangle className="size-3" />,
    label: "Status unavailable",
  },
};

function KeyStatusBadge({ status }: { status: string }) {
  const shown = KEY_STATUS[status] ?? KEY_STATUS.unvalidated;
  return (
    <Badge variant={shown.variant} className="gap-1 shrink-0">
      {shown.icon} {shown.label}
    </Badge>
  );
}

function Field({
  label,
  value,
  hint,
  muted,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  muted?: boolean;
  className?: string;
}) {
  return (
    <div className={`rounded-md border border-border bg-card p-3 ${className ?? ""}`}>
      <div className="text-2xs uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={
          muted
            ? "text-sm text-muted-foreground mt-1 break-words"
            : "text-sm font-medium mt-1 break-words"
        }
      >
        {value}
        {hint && (
          <span className="ml-1.5 text-xs text-muted-foreground font-normal">
            {hint}
          </span>
        )}
      </div>
    </div>
  );
}
