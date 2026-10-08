import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import type { DomainSurface } from "@/api/platform";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { formatError } from "@/lib/errors";
import { SURFACE_OPTIONS, hostnameProblem, normaliseHostname } from "./domainMeta";

/** Hostname + which sites it serves. Used to add an address in the platform
 *  console and to request one in the firm console. A refusal (a hostname
 *  already in use, an invalid one) stays beside the form. */
export function DomainRequestForm({
  idPrefix,
  submitLabel,
  pending,
  onSubmit,
}: {
  idPrefix: string;
  submitLabel: string;
  pending: boolean;
  onSubmit: (hostname: string, surface: DomainSurface) => Promise<void>;
}) {
  const [hostname, setHostname] = useState("");
  const [surface, setSurface] = useState<DomainSurface>("all");
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = normaliseHostname(hostname);
    const problem = hostnameProblem(value);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    try {
      await onSubmit(value, surface);
      setHostname("");
      setSurface("all");
    } catch (e) {
      setError(formatError(e));
    }
  };

  const errorId = `${idPrefix}-error`;
  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-2">
      <div className="grid grid-cols-1 items-end gap-2 md:grid-cols-[minmax(0,1fr)_220px_auto]">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-hostname`}>Web address</Label>
          <Input
            id={`${idPrefix}-hostname`}
            value={hostname}
            spellCheck={false}
            autoComplete="off"
            inputMode="url"
            placeholder="portal.yourbroker.com"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(e) => {
              setHostname(e.target.value);
              setError(null);
            }}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-surface`}>Serves</Label>
          <NativeSelect
            id={`${idPrefix}-surface`}
            className="h-9"
            value={surface}
            onChange={(e) => setSurface(e.target.value as DomainSurface)}
          >
            {SURFACE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </NativeSelect>
        </div>
        <Button type="submit" loading={pending}>
          <Plus className="size-4" />
          {submitLabel}
        </Button>
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
    </form>
  );
}
