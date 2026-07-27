import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  KeyRound,
  Loader2,
  Pencil,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  useAIConfig,
  useAIStatus,
  useDeleteAIConfig,
  useMe,
  usePutAIConfig,
  useTestAIConfig,
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
import { formatError } from "@/lib/errors";
import { AIUsageTile } from "@/components/schema/AIUsageTile";
import { PlatformAILimitsCard } from "@/components/configuration/PlatformAILimitsCard";
import { PlatformAIProviderCard } from "@/components/configuration/PlatformAIProviderCard";
import {
  DEFAULT_VERTEX_MODEL,
  VertexKeyDrawer,
  type VertexKeyDraft,
} from "@/components/configuration/VertexKeyDrawer";
import { PageGuide } from "@/components/ui/page-guide";

export function AIProviderPage() {
  const { data: me, isPending: meLoading } = useMe();
  const isSystemAdmin = me?.role === "system_admin";
  // Wait for the role before deciding: on a hard reload `me` is briefly
  // undefined, which read as "not a system admin" and fired /ai-config — a
  // broker_admin-only endpoint — producing a spurious 403 toast for system
  // admins every time they landed here directly.
  const { data: config, isLoading } = useAIConfig(!meLoading && !isSystemAdmin);
  const { data: status } = useAIStatus();
  const put = usePutAIConfig();
  const remove = useDeleteAIConfig();
  const test = useTestAIConfig();

  const [open, setOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const source = status?.source ?? "none";
  const SOURCE_BADGES: Record<string, JSX.Element> = {
    byok: (
      <Badge variant="good" className="gap-1">
        <ShieldCheck className="size-3" /> Using this company's key
      </Badge>
    ),
    platform: (
      <Badge variant="info" className="gap-1">
        <Cloud className="size-3" /> Using the platform key
      </Badge>
    ),
    env: (
      <Badge variant="info" className="gap-1">
        <Cloud className="size-3" /> Using server credentials
      </Badge>
    ),
    none: (
      <Badge variant="warn" className="gap-1">
        <AlertTriangle className="size-3" /> Not configured
      </Badge>
    ),
  };
  const sourceBadge = SOURCE_BADGES[source] ?? SOURCE_BADGES.none;

  const submit = async (draft: VertexKeyDraft) => {
    try {
      await put.mutateAsync({
        provider: "vertex",
        endpoint: draft.location.trim() || null,
        model: draft.model.trim() || null,
        api_key: draft.serviceAccountJson,
      });
      toast.success(config ? "Company AI key updated" : "Company AI key saved");
      setOpen(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const runTest = async (draft?: VertexKeyDraft) => {
    try {
      const result = await test.mutateAsync(
        draft
          ? {
              provider: "vertex",
              endpoint: draft.location.trim() || null,
              model: draft.model.trim() || null,
              api_key: draft.serviceAccountJson || null,
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
    <div className="space-y-4">
      {/* Controlled drawer — opened by the Configure button inside the company
          override card below (no visible trigger here). */}
      <VertexKeyDrawer
        open={open}
        onOpenChange={setOpen}
        title={config ? "Update company AI key" : "Configure company AI key"}
        scopeNote={
          <>
            Provider:{" "}
            <span className="font-medium text-foreground">Google Vertex — Gemini</span>
            , Singapore (data-resident). This key applies to{" "}
            <span className="font-medium text-foreground">this company only</span> and
            overrides the platform key.
          </>
        }
        initial={config ? { location: config.endpoint, model: config.model } : null}
        storedKeyMasked={config?.key_masked}
        saving={put.isPending}
        testing={test.isPending}
        onSave={(draft) => void submit(draft)}
        onTest={(draft) => void runTest(draft)}
      />

      <AIUsageTile />

      {isSystemAdmin && (
        <>
          <PlatformAIProviderCard />
          <PlatformAILimitsCard />
        </>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle>This company's AI key</CardTitle>
              <CardDescription>
                Optional override. Leave it unset and this company runs on the
                platform key; set one to bill a separate Google project and
                track its spend independently.
              </CardDescription>
            </div>
            {sourceBadge}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isSystemAdmin ? (
            <div className="text-sm text-muted-foreground p-5 text-center border border-dashed border-border rounded-md">
              A company's own key is managed by that company's broker admin. As
              system-admin you set the{" "}
              <span className="font-medium text-foreground">Platform AI key</span>{" "}
              above, which every company without an override uses.
            </div>
          ) : isLoading ? (
            <div className="text-sm text-muted-foreground">Loading…</div>
          ) : config ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Provider" value={config.provider} />
              <Field
                label="Model"
                value={config.model ?? DEFAULT_VERTEX_MODEL}
                muted={!config.model}
                hint={!config.model ? "(default)" : undefined}
              />
              {config.endpoint && (
                <Field
                  label="GCP location"
                  value={config.endpoint}
                  className="md:col-span-2 font-mono text-xs"
                />
              )}
              <Field
                label="Service account"
                value={config.key_masked}
                hint={`fp ${config.key_fingerprint}`}
              />
              <Field
                label="Last validated"
                value={
                  config.last_validated_at
                    ? new Date(config.last_validated_at).toLocaleString()
                    : "Never"
                }
                muted={!config.last_validated_at}
              />
              {config.last_validation_error && (
                <div className="md:col-span-2 rounded-md border border-error/40 bg-error-soft/40 p-3 text-sm text-error flex items-start gap-2">
                  <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                  <span>{config.last_validation_error}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground p-5 text-center border border-dashed border-border rounded-md">
              {source === "none" ? (
                <>
                  No key for this company, and no platform key is set — AI
                  features stay disabled until one of them is configured. Ask
                  your platform administrator, or set a key for this company.
                </>
              ) : (
                <>
                  No key for this company — it runs on the{" "}
                  {source === "env" ? "server's" : "platform"} credentials.
                  Configure one to use a different Google project.
                </>
              )}
            </div>
          )}
          {!isSystemAdmin && (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {config && (
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
              <Button onClick={() => setOpen(true)}>
                {config ? (
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
          )}
        </CardContent>
      </Card>

      <PageGuide
        purpose="Configure the Google Vertex (Gemini) AI backend for rule suggestion, roster profiling and claims review. The platform key (system-admin) is the default for every company; a company can override it with its own Vertex service-account JSON."
        connections={[
          { label: "→ Roster profiling", description: "AI profiling uses this provider to analyze roster columns" },
          { label: "→ AI review queue", description: "AI-suggested matching rules are generated via this provider" },
          { label: "→ Diagnostics", description: "AI spend and usage are tracked in the audit log" },
        ]}
      />

      <AlertDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear this company's AI key?"
        description={
          <>
            The encrypted key will be deleted and this company's AI calls will
            fall back to the platform key. The audit log keeps a record of the
            change. This cannot be undone.
          </>
        }
        confirmLabel="Clear key"
        loading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync();
            toast.success("Company key cleared — falling back to the platform key");
            setConfirmClear(false);
          } catch (err) {
            toast.error(formatError(err));
          }
        }}
      />
    </div>
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
    <div
      className={`rounded-md border border-border bg-card p-3 ${className ?? ""}`}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
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
