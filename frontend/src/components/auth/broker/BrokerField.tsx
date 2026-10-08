import { useState, type InputHTMLAttributes, type ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/cn";

type NativeInput = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "value" | "onChange" | "type">;

/** A labelled input on the broker sign-in surface, with its guidance and error
 *  announced through `aria-describedby`. Password fields get a show/hide
 *  control. Styled by `.broker-login` (broker-login.css). */
export function BrokerField({
  id,
  label,
  value,
  onChange,
  type = "text",
  optional = false,
  hint,
  error,
  className,
  ...input
}: NativeInput & {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "password";
  optional?: boolean;
  hint?: ReactNode;
  error?: string | null;
}) {
  const [visible, setVisible] = useState(false);
  const revealable = type === "password";
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className="broker-login__field">
      <Label htmlFor={id}>
        {label}
        {optional && <span className="broker-login__optional"> (optional)</span>}
      </Label>
      <div className="broker-login__control">
        <Input
          {...input}
          id={id}
          type={revealable && visible ? "text" : type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(revealable && "broker-login__input--revealable", className)}
        />
        {revealable && (
          <button
            type="button"
            className="broker-login__reveal"
            aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            aria-pressed={visible}
            aria-controls={id}
            onClick={() => setVisible(!visible)}
          >
            {visible ? <EyeOff size={20} aria-hidden="true" /> : <Eye size={20} aria-hidden="true" />}
          </button>
        )}
      </div>
      {hint && <p id={hintId} className="broker-login__hint">{hint}</p>}
      {error && <p id={errorId} className="broker-login__field-error" role="alert">{error}</p>}
    </div>
  );
}
