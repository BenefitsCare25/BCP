import { useState, type InputHTMLAttributes } from "react";
import { Input } from "@/components/ui/input";

type Props = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "onBlur" | "type"
> & {
  /** The override when set, else the recommendation it falls back to. */
  value: number | null | undefined;
  onValueChange: (value: string) => void;
};

/** A money input whose shown value falls back to a recommendation.
 *
 * Emptying the field drops the override, so a plainly controlled input would
 * snap straight back to the recommendation mid-edit and the next digits would
 * append to it ("1200" → "" → typing 800 saved 12008). The typed text is kept
 * as a local draft until focus leaves; an empty field then shows the
 * recommendation again, which is what clearing it means. */
export function PriceTagInput({ value, onValueChange, min = "0", step = "0.01", ...props }: Props) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <Input
      {...props}
      type="number"
      min={min}
      step={step}
      value={draft ?? value ?? ""}
      onChange={(event) => {
        setDraft(event.target.value);
        onValueChange(event.target.value);
      }}
      onBlur={() => setDraft(null)}
    />
  );
}
