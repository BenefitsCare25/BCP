import { usePortalTranslation } from "@/i18n/portal";
import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PASSWORD_MAX_LENGTH } from "@/lib/loginValidation";

export function LoginPasswordField({ id, value, onChange, error }: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  const pt = usePortalTranslation();
  const [visible, setVisible] = useState(false);
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{pt("Password")}</Label>
      <div className="relative">
        <Input id={id} name="password" required maxLength={PASSWORD_MAX_LENGTH} aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} type={visible ? "text" : "password"} autoComplete="current-password" value={value} onChange={(event) => onChange(event.target.value)} className="portal-login__password" />
        <button type="button" className="portal-login__reveal" aria-label={visible ? pt("Hide password") : pt("Show password")} title={visible ? pt("Hide password") : pt("Show password")} aria-pressed={visible} onClick={() => setVisible(!visible)}>
          {visible ? <EyeOff size={20} aria-hidden="true" /> : <Eye size={20} aria-hidden="true" />}
        </button>
      </div>
      {error && <p id={`${id}-error`} className="text-sm text-error" role="alert">{pt(error)}</p>}
    </div>
  );
}
