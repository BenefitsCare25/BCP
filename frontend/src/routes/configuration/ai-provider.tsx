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
  Wand2,
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
import { formatError } from "@/lib/errors";
import { AIUsageTile } from "@/components/schema/AIUsageTile";
import { PlatformAILimitsCard } from "@/components/configuration/PlatformAILimitsCard";
import { PageGuide } from "@/components/ui/page-guide";
import type { AIConfig } from "@/types";

// Vertex/Gemini is the sole provider (AWS Bedrock + Anthropic were removed).
const DEFAULT_VERTEX_LOCATION = "asia-southeast1";
const DEFAULT_VERTEX_MODEL = "gemini-2.5-flash";

interface Draft {
  // endpoint = GCP location; api_key = the service-account JSON key.
  endpoint: string;
  model: string;
  api_key: string;
}

const EMPTY_DRAFT: Draft = {
  endpoint: DEFAULT_VERTEX_LOCATION,
  model: "",
  api_key: "",
};

function fromConfig(config: AIConfig): Draft {
  return {
    endpoint: config.endpoint ?? DEFAULT_VERTEX_LOCATION,
    model: config.model ?? "",
    api_key: "",
  };
}

// A Vertex BYOK key is the service-account JSON file — sanity-check the markers
// so we don't send an obviously-wrong value (an API key, a truncated paste).
function isValidServiceAccountJson(value: string): boolean {
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

function isValidDraft(draft: Draft): boolean {
  if (!draft.endpoint.trim()) return false; // GCP location
  return isValidServiceAccountJson(draft.api_key); // SA JSON is the key
}

export function AIProviderPage() {
  const { data: me } = useMe();
  const isSystemAdmin = me?.role === "system_admin";
  const { data: config, isLoading } = useAIConfig(!isSystemAdmin);
  const { data: status } = useAIStatus();
  const put = usePutAIConfig();
  const remove = useDeleteAIConfig();
  const test = useTestAIConfig();

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [confirmClear, setConfirmClear] = useState(false);

  const onSheetOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) {
      setDraft(config ? fromConfig(config) : EMPTY_DRAFT);
    }
  };

  const source = status?.source ?? "none";
  const SOURCE_BADGES: Record<string, JSX.Element> = {
    byok: (
      <Badge variant="good" className="gap-1">
        <ShieldCheck className="size-3" /> Using your key (BYOK)
      </Badge>
    ),
    env: (
      <Badge variant="info" className="gap-1">
        <Cloud className="size-3" /> Using platform key
      </Badge>
    ),
    none: (
      <Badge variant="warn" className="gap-1">
        <AlertTriangle className="size-3" /> Not configured
      </Badge>
    ),
  };
  const sourceBadge = SOURCE_BADGES[source] ?? SOURCE_BADGES.none;

  const submit = async () => {
    try {
      await put.mutateAsync({
        provider: "vertex",
        endpoint: draft.endpoint.trim() || DEFAULT_VERTEX_LOCATION,
        model: draft.model.trim() || null,
        api_key: draft.api_key,
      });
      toast.success(config ? "AI provider updated" : "AI provider saved");
      setOpen(false);
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  const onTest = async () => {
    try {
      const result = await test.mutateAsync(
        open
          ? {
              provider: "vertex",
              endpoint: draft.endpoint.trim() || DEFAULT_VERTEX_LOCATION,
              model: draft.model.trim() || null,
              api_key: draft.api_key || null,
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
    <div className="space-y-4 max-w-7xl">
      {/* Controlled drawer — opened by the Configure button inside the Current
          configuration card below (no visible trigger here). */}
      <Sheet open={open} onOpenChange={onSheetOpenChange}>
        <SheetContent side="right">
            <SheetHeader>
              <SheetTitle className="flex items-center gap-1.5">
                {config ? "Update AI provider" : "Configure AI provider"}
                <InfoHint>
                  The service-account key is encrypted at rest. Other fields are
                  stored in plain text.
                </InfoHint>
              </SheetTitle>
            </SheetHeader>
            <SheetBody className="space-y-4">
              <div className="rounded-md border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
                Provider: <span className="font-medium text-foreground">Google Vertex — Gemini</span>,
                Singapore (data-resident). This is the only supported provider.
              </div>
              <div className="flex flex-col gap-1.5">
                <FieldLabel hint="The Vertex AI location. Keep this in Singapore (asia-southeast1) so claim data stays in-region — the backend refuses other regions in prod.">
                  GCP location
                </FieldLabel>
                <Input
                  value={draft.endpoint}
                  onChange={(e) =>
                    setDraft({ ...draft, endpoint: e.target.value })
                  }
                  placeholder={DEFAULT_VERTEX_LOCATION}
                />
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
                  value={draft.api_key}
                  onChange={(e) =>
                    setDraft({ ...draft, api_key: e.target.value })
                  }
                  placeholder={
                    config
                      ? `Stored: ${config.key_masked} — paste a new key file to replace`
                      : '{ "type": "service_account", "project_id": "inspro-ai", … }'
                  }
                  rows={6}
                  autoComplete="off"
                  spellCheck={false}
                  className="flex w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-xs text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:border-ring"
                />
                {draft.api_key.trim() !== "" &&
                  !isValidServiceAccountJson(draft.api_key) && (
                    <p className="text-xs text-error">
                      This must be the full service-account JSON key (with{" "}
                      <code>type</code>, <code>project_id</code>,{" "}
                      <code>private_key</code> and <code>client_email</code>).
                    </p>
                  )}
              </div>
              <Button
                variant="outline"
                onClick={onTest}
                disabled={test.isPending || !draft.api_key}
                title={
                  draft.api_key
                    ? undefined
                    : config
                      ? "Re-paste the key to test — the stored key is never returned to the browser"
                      : "Paste a service-account key to test"
                }
                className="w-full justify-center"
              >
                {test.isPending ? (
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
                onClick={submit}
                disabled={put.isPending || !isValidDraft(draft)}
              >
                {put.isPending && <Loader2 className="size-4 animate-spin" />}
                Save
              </Button>
            </SheetFooter>
        </SheetContent>
      </Sheet>

      <AIUsageTile />

      {isSystemAdmin && <PlatformAILimitsCard />}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle>Current configuration</CardTitle>
              <CardDescription>
                Drives every AI suggestion / rule generation call for this
                tenant.
              </CardDescription>
            </div>
            {sourceBadge}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isSystemAdmin ? (
            <div className="text-sm text-muted-foreground p-5 text-center border border-dashed border-border rounded-md">
              Per-company AI keys (BYOK) are managed by each company's broker
              admin. As system-admin you manage the shared{" "}
              <span className="font-medium text-foreground">
                Platform AI limits
              </span>{" "}
              above.
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
              {source === "env" ? (
                <>
                  No BYOK configured — the platform's shared Vertex credentials
                  (<code>INSPRO_AI_PROVIDER=vertex</code> +{" "}
                  <code>VERTEX_PROJECT</code> / Google ADC) are in use. Configure
                  your own key to track spend separately and use a different
                  project.
                </>
              ) : (
                <>
                  Neither BYOK nor platform credentials are configured. AI
                  features will be disabled until a key is set.
                </>
              )}
            </div>
          )}
          {!isSystemAdmin && (
            <div className="flex justify-end">
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

      {config && (
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onTest}
            disabled={test.isPending}
          >
            {test.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="size-3.5" />
            )}
            Test stored config
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-error hover:text-error"
            onClick={() => setConfirmClear(true)}
            disabled={remove.isPending}
          >
            <Trash2 className="size-3.5" /> Clear BYOK
          </Button>
        </div>
      )}

      <PageGuide
        purpose="Configure the Google Vertex (Gemini) AI backend for rule suggestion, roster profiling and claims review. Use platform credentials or bring your own key (BYOK) — a Vertex service-account JSON."
        connections={[
          { label: "→ Roster profiling", description: "AI profiling uses this provider to analyze roster columns" },
          { label: "→ AI review queue", description: "AI-suggested matching rules are generated via this provider" },
          { label: "→ Diagnostics", description: "AI spend and usage are tracked in the audit log" },
        ]}
      />

      <AlertDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear BYOK configuration?"
        description={
          <>
            The encrypted key will be deleted and AI calls for this tenant will
            fall back to the platform's shared credentials. The audit log
            keeps a record of the change. This cannot be undone.
          </>
        }
        confirmLabel="Clear configuration"
        loading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync();
            toast.success("BYOK cleared — falling back to platform key");
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
