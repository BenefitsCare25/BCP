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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { FieldLabel, InfoHint } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";
import { AIUsageTile } from "@/components/schema/AIUsageTile";
import { PageGuide } from "@/components/ui/page-guide";
import type { AIConfig, AIProvider } from "@/types";

const DEFAULT_MODEL = "claude-sonnet-4-6";

interface Draft {
  provider: AIProvider;
  endpoint: string;
  model: string;
  api_key: string;
}

const EMPTY_DRAFT: Draft = {
  provider: "azure_foundry",
  endpoint: "",
  model: "",
  api_key: "",
};

function fromConfig(config: AIConfig): Draft {
  return {
    provider: config.provider,
    endpoint: config.endpoint ?? "",
    model: config.model ?? "",
    api_key: "",
  };
}

function isValidFoundryEndpoint(url: string): boolean {
  // Accept both URL formats — backend normalises both to .../anthropic/
  const trimmed = url.trim().toLowerCase();
  return trimmed.includes("/anthropic") || trimmed.includes("/api/projects/");
}

function isValidDraft(draft: Draft): boolean {
  if (draft.provider === "azure_foundry") {
    if (!draft.endpoint.trim()) return false;
    if (!isValidFoundryEndpoint(draft.endpoint)) return false;
  }
  return draft.api_key.length >= 8;
}

export function AIProviderPage() {
  const { data: config, isLoading } = useAIConfig();
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
      <Badge variant="primary" className="gap-1">
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
        provider: draft.provider,
        endpoint: draft.provider === "azure_foundry" ? draft.endpoint.trim() : null,
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
              provider: draft.provider,
              endpoint:
                draft.provider === "azure_foundry"
                  ? draft.endpoint.trim() || null
                  : null,
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
      <div className="flex justify-end">
        <Sheet open={open} onOpenChange={onSheetOpenChange}>
          <SheetTrigger asChild>
            <Button>
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
          </SheetTrigger>
          <SheetContent side="right">
            <SheetHeader>
              <SheetTitle className="flex items-center gap-1.5">
                {config ? "Update AI provider" : "Configure AI provider"}
                <InfoHint>
                  The API key is encrypted at rest. Other fields are stored in
                  plain text.
                </InfoHint>
              </SheetTitle>
            </SheetHeader>
            <SheetBody className="space-y-4">
              <div className="flex flex-col gap-1.5">
                <FieldLabel hint="Azure AI Foundry routes calls through your Azure resource (recommended for prod); Anthropic calls the API directly.">
                  Provider
                </FieldLabel>
                <Select
                  value={draft.provider}
                  onValueChange={(v) =>
                    setDraft({ ...draft, provider: v as AIProvider })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="azure_foundry">
                      Azure AI Foundry (recommended for prod)
                    </SelectItem>
                    <SelectItem value="anthropic">
                      Anthropic (direct)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {draft.provider === "azure_foundry" && (
                <div className="flex flex-col gap-1.5">
                  <FieldLabel
                    hint={
                      <>
                        Accepts either format — resource endpoint{" "}
                        <code>…/anthropic/</code> or project URL{" "}
                        <code>…/api/projects/&lt;id&gt;</code>. The backend
                        resolves both to the Anthropic-compatible path
                        automatically.
                      </>
                    }
                  >
                    Endpoint URL
                  </FieldLabel>
                  <Input
                    value={draft.endpoint}
                    onChange={(e) =>
                      setDraft({ ...draft, endpoint: e.target.value })
                    }
                    placeholder="https://<resource>.services.ai.azure.com/anthropic/"
                    className={
                      draft.endpoint && !isValidFoundryEndpoint(draft.endpoint)
                        ? "border-error focus-visible:ring-error"
                        : ""
                    }
                  />
                  {draft.endpoint && !isValidFoundryEndpoint(draft.endpoint) && (
                    <p className="text-xs text-error">
                      Paste either the resource endpoint ending in{" "}
                      <code>/anthropic/</code> or your project URL{" "}
                      <code>.../api/projects/&lt;id&gt;</code> — both are
                      accepted.
                    </p>
                  )}
                </div>
              )}
              <div className="flex flex-col gap-1.5">
                <FieldLabel
                  hint={
                    draft.provider === "azure_foundry" ? (
                      <>
                        Must match your Azure AI Foundry{" "}
                        <strong>deployment name</strong> exactly (not the
                        Anthropic model ID). Find it under your project →
                        Deployments. Leave blank to use the default{" "}
                        <code>{DEFAULT_MODEL}</code>.
                      </>
                    ) : (
                      <>
                        Anthropic model ID. Leave blank to use{" "}
                        <code>{DEFAULT_MODEL}</code>.
                      </>
                    )
                  }
                >
                  {draft.provider === "azure_foundry"
                    ? "Deployment name"
                    : "Model (optional)"}
                </FieldLabel>
                <Input
                  value={draft.model}
                  onChange={(e) =>
                    setDraft({ ...draft, model: e.target.value })
                  }
                  placeholder={DEFAULT_MODEL}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <FieldLabel hint="Encrypted with Fernet (AES-128-CBC + HMAC) and never returned in any API response.">
                  API key
                </FieldLabel>
                <Input
                  type="password"
                  value={draft.api_key}
                  onChange={(e) =>
                    setDraft({ ...draft, api_key: e.target.value })
                  }
                  placeholder={
                    config
                      ? `Stored: ${config.key_masked} — paste a new key to replace`
                      : "sk-ant-... or Azure access key"
                  }
                  autoComplete="off"
                />
              </div>
              <Button
                variant="outline"
                onClick={onTest}
                disabled={test.isPending || !draft.api_key}
                title={
                  draft.api_key
                    ? undefined
                    : config
                      ? "Re-enter the API key to test — the stored key is never returned to the browser"
                      : "Enter an API key to test"
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
      </div>

      <AIUsageTile />

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
        <CardContent>
          {isLoading ? (
            <div className="text-sm text-muted-foreground">Loading…</div>
          ) : config ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Provider" value={config.provider} />
              <Field
                label="Model"
                value={config.model ?? DEFAULT_MODEL}
                muted={!config.model}
                hint={!config.model ? "(default)" : undefined}
              />
              {config.endpoint && (
                <Field
                  label="Endpoint"
                  value={config.endpoint}
                  className="md:col-span-2 font-mono text-xs"
                />
              )}
              <Field
                label="API key"
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
                  No BYOK configured — the platform's shared{" "}
                  <code>AZURE_FOUNDRY_API_KEY</code> is in use. Configure your
                  own key to track spend separately and use a different model.
                </>
              ) : (
                <>
                  Neither BYOK nor platform credentials are configured. AI
                  features will be disabled until a key is set.
                </>
              )}
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
        purpose="Configure the AI backend for rule suggestion and roster profiling. Use platform credentials or bring your own key (BYOK) for Azure AI Foundry or Anthropic direct."
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
