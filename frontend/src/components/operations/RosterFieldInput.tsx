import { useId } from "react";
import { Input } from "@/components/ui/input";
import type { EmployeeRosterField } from "@/types";

export function validRosterDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function rosterFieldType(field: EmployeeRosterField, original: unknown): string {
  if (original != null && typeof original === "object") return "json";
  if (typeof original === "boolean") return "boolean";
  // Declared string identifiers remain strings even when an old upload stored
  // a number. Bank/member IDs must not gain numeric spinners or lose zeros.
  return field.data_type;
}

export function rosterDraftValue(field: EmployeeRosterField, original: unknown): string {
  if (field.data_type === "date" && field.edit_value != null) return field.edit_value;
  if (original == null) return "";
  return typeof original === "object" ? JSON.stringify(original, null, 2) : String(original);
}

const NUMERIC = new Set(["decimal", "number", "integer", "float"]);

export function RosterFieldInput({ field, original, value, disabled, onChange }: {
  field: EmployeeRosterField;
  original: unknown;
  value: string;
  disabled: boolean;
  onChange: (value: string, badInput?: boolean) => void;
}) {
  const id = useId();
  const type = rosterFieldType(field, original);
  const numeric = NUMERIC.has(type);
  const nativeType = type === "date" ? "date" : numeric ? "number"
    : type === "email" ? "email" : type === "tel" ? "tel" : "text";
  const options = type === "boolean" ? ["true", "false"] : field.enum_values ?? [];
  const choice = type === "boolean" || (type === "enum" && options.length > 0);
  const uploaded = rosterDraftValue(field, original);
  const unsupportedUpload = uploaded !== "" && (
    (type === "date" && !validRosterDate(uploaded)) ||
    (numeric && !Number.isFinite(Number(uploaded)))
  );
  const displayed = (type === "date" && value !== "" && !validRosterDate(value)) ||
    (numeric && value !== "" && !Number.isFinite(Number(value))) ? "" : value;
  const shared = {
    id, disabled, "aria-label": field.display_name,
    "aria-describedby": unsupportedUpload ? `${id}-uploaded` : undefined,
  };
  return (
    <label className="block break-words text-xs text-muted-foreground" htmlFor={id}>
      {field.display_name}
      {type === "json" || type === "textarea" ? (
        <textarea {...shared} value={value} placeholder="Not provided" rows={3}
          onChange={(event) => onChange(event.target.value)}
          className="focus-ring mt-1.5 w-full rounded-md border border-input bg-card px-3 py-2 text-base text-foreground sm:text-sm" />
      ) : choice ? (
        <select {...shared} value={value} onChange={(event) => onChange(event.target.value)}
          className="focus-ring mt-1.5 h-9 w-full rounded-md border border-input bg-card px-3 text-base text-foreground sm:text-sm">
          <option value="">Not provided</option>
          {uploaded !== "" && !options.includes(uploaded) && <option value={uploaded}>{uploaded} (as uploaded)</option>}
          {options.map((option) => <option key={option} value={option}>
            {type === "boolean" ? (option === "true" ? "Yes" : "No") : option}
          </option>)}
        </select>
      ) : (
        <Input {...shared} type={nativeType} value={displayed} placeholder="Not provided"
          step={numeric ? (type === "integer" ? 1 : "any") : undefined}
          // Native input events also report incomplete numbers whose DOM value
          // stays empty, so React's value-based change event may not fire.
          onInput={(event) => onChange(event.currentTarget.value, event.currentTarget.validity.badInput)}
          onChange={(event) => onChange(event.target.value, event.target.validity.badInput)}
          className="mt-1.5 min-w-0 text-base sm:text-sm [&::-webkit-calendar-picker-indicator]:ml-auto [&::-webkit-calendar-picker-indicator]:cursor-pointer" />
      )}
      {unsupportedUpload && <span id={`${id}-uploaded`} className="mt-1.5 block text-xs text-muted-foreground">
        Uploaded value: {String(original)}. {type === "date" ? "Select a date" : "Enter a number"} to replace it.
      </span>}
    </label>
  );
}
