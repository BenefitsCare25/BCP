/** Login identifier input that detects email vs HR user id and shows an inline
 * hint. Detection is UX only — the server accepts either form regardless. */
import { AtSign, IdCard } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type IdentifierKind = "email" | "hr-id" | "unknown";

const HR_ID_RE = /^HR-[A-Z0-9]{4,}$/i;

export function detectIdentifier(value: string): IdentifierKind {
  const v = value.trim();
  if (!v) return "unknown";
  if (v.includes("@")) return "email";
  if (HR_ID_RE.test(v) || v.toUpperCase().startsWith("HR-")) return "hr-id";
  return "unknown";
}

export function IdentifierField({
  value,
  onChange,
  id = "hr-identifier",
  autoFocus,
}: {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  autoFocus?: boolean;
}) {
  const kind = detectIdentifier(value);
  const Icon = kind === "hr-id" ? IdCard : AtSign;
  return (
    <div className="space-y-1.5">
      <Label
        htmlFor={id}
        className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
      >
        Email or HR ID
      </Label>
      <div className="relative">
        <Icon className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-muted-foreground" />
        <Input
          id={id}
          type="text"
          autoComplete="username"
          spellCheck={false}
          placeholder="you@company.com  or  HR-7Q2M8K"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoFocus={autoFocus}
          className="h-12 pl-11"
        />
      </div>
      {kind !== "unknown" && (
        <p className="text-xs text-muted-foreground">
          {kind === "email" ? "Signing in with your email" : "Signing in with your HR ID"}
        </p>
      )}
    </div>
  );
}
