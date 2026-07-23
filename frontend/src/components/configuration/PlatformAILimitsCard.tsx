import { useEffect, useRef, useState } from "react";
import { Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import {
  usePlatformAISettings,
  useSetPlatformAISettings,
  type PlatformAISettings,
} from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldLabel } from "@/components/ui/tooltip";
import { formatError } from "@/lib/errors";

type FieldKey = keyof PlatformAISettings;

interface FieldSpec {
  key: FieldKey;
  label: string;
  hint: string;
  placeholder: string;
  disabledMeans: string;
}

const FIELDS: FieldSpec[] = [
  {
    key: "platform_monthly_token_cap",
    label: "Platform token cap (all companies / month)",
    hint: "Hard backstop across every company on the shared AI key. Once the combined monthly non-cached tokens reach this, new AI calls are blocked for everyone until next month. Keep it below your Google project's quota.",
    placeholder: "e.g. 300000000",
    disabledMeans: "no platform cap",
  },
  {
    key: "default_monthly_token_budget",
    label: "Default per-company monthly budget",
    hint: "Applied to any company that hasn't set its own budget on the AI usage tile. A company's own budget always wins. Stops one company that was left on 'unlimited' from draining the shared key.",
    placeholder: "e.g. 5000000",
    disabledMeans: "no default (companies stay unlimited)",
  },
  {
    key: "max_concurrent_calls",
    label: "Max concurrent AI calls (per server)",
    hint: "Backpressure for bursts — e.g. 100 members submitting at once. Extra calls queue instead of stampeding the shared quota and tripping provider rate limits. Effective total is this × the number of app instances.",
    placeholder: "e.g. 15",
    disabledMeans: "unbounded",
  },
];

const EMPTY: Record<FieldKey, string> = {
  platform_monthly_token_cap: "",
  default_monthly_token_budget: "",
  max_concurrent_calls: "",
};

/** System-admin-only controls for the shared AI key. Render behind a role gate. */
export function PlatformAILimitsCard() {
  const { data, isLoading } = usePlatformAISettings();
  const save = useSetPlatformAISettings();
  const [draft, setDraft] = useState<Record<FieldKey, string>>(EMPTY);
  const seeded = useRef(false);

  // Seed the inputs from the server value ONCE, on first load. Re-seeding on
  // every `data` change would clobber in-progress typing whenever React Query
  // refetches (e.g. on window focus). 0 (disabled) shows as blank.
  useEffect(() => {
    if (!data || seeded.current) return;
    seeded.current = true;
    setDraft({
      platform_monthly_token_cap: data.platform_monthly_token_cap
        ? String(data.platform_monthly_token_cap)
        : "",
      default_monthly_token_budget: data.default_monthly_token_budget
        ? String(data.default_monthly_token_budget)
        : "",
      max_concurrent_calls: data.max_concurrent_calls
        ? String(data.max_concurrent_calls)
        : "",
    });
  }, [data]);

  const onSave = async () => {
    const payload = {} as PlatformAISettings;
    for (const f of FIELDS) {
      const raw = draft[f.key].trim();
      const value = raw === "" ? 0 : Number(raw);
      if (!Number.isInteger(value) || value < 0) {
        toast.error(`${f.label}: enter a whole number (blank = disabled).`);
        return;
      }
      payload[f.key] = value;
    }
    try {
      await save.mutateAsync(payload);
      toast.success("Platform AI limits saved");
    } catch (err) {
      toast.error(formatError(err));
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldAlert className="size-4 text-muted-foreground" />
          <CardTitle>Platform AI limits</CardTitle>
        </div>
        <CardDescription>
          All companies share one AI key, so these caps protect the shared quota
          across every company. System-admin only. Leave a field blank (or{" "}
          <code>0</code>) to disable that limit.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              {FIELDS.map((f) => (
                <div key={f.key} className="space-y-1.5">
                  <FieldLabel hint={f.hint}>{f.label}</FieldLabel>
                  <Input
                    type="number"
                    min={0}
                    inputMode="numeric"
                    value={draft[f.key]}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, [f.key]: e.target.value }))
                    }
                    placeholder={f.placeholder}
                  />
                  <p className="text-xs text-muted-foreground">
                    Blank = {f.disabledMeans}.
                  </p>
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <Button onClick={onSave} disabled={save.isPending}>
                {save.isPending && <Loader2 className="size-3.5 animate-spin" />}
                Save platform limits
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
