import { useId } from "react";
import { FieldLabel } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Firm target for a system_admin's create/invite action, and for the firm a
 * platform admin joins when moved to a firm role.
 *
 * A firm's own administrators always act on their own firm, and a single-firm
 * platform has exactly one answer (the backend resolves it for create/invite;
 * the role change fills it in itself), so this renders NOTHING in either case —
 * showing a select with one option is noise. It appears only when there really
 * are several firms and the choice is the admin's to make. */
export function FirmPicker({
  firms, value, onChange,
}: {
  firms: { id: string; name: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  const id = useId();
  if (firms.length < 2) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel
        htmlFor={id}
        hint="Which broker firm this belongs to. Shown because you administer more than one."
      >
        Broker firm
      </FieldLabel>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id}><SelectValue placeholder="Select a firm" /></SelectTrigger>
        <SelectContent>
          {firms.map((f) => (
            <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
