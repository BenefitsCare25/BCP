import { Calendar } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatPolicyRange, isPastPolicyPeriod } from "@/lib/policy-year";
import type { PolicyYear } from "@/types";

/**
 * Configuration-scoped benefit-year picker (the global top-bar picker was
 * removed). Selecting a non-current year views its config; a past (ended) year
 * is read-only. The `config-nav` class keeps it clickable under the read-only
 * wrapper — you must be able to switch back to the current year to leave the
 * read-only view.
 */
export function ViewingYearSelect({
  value,
  years,
  onChange,
}: {
  value: string;
  years: PolicyYear[];
  onChange: (id: string) => void;
}) {
  const viewed = years.find((y) => y.id === value) ?? null;
  const readOnly = viewed ? isPastPolicyPeriod(viewed.coverage_end) : false;
  return (
    <div className="config-nav flex items-center gap-2">
      <Calendar className="size-4 text-muted-foreground" />
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8 w-[290px] whitespace-nowrap">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {years.map((y) => (
            <SelectItem key={y.id} value={y.id}>
              {formatPolicyRange(y.coverage_start, y.coverage_end)}
              {y.status === "active" ? " · current" : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {readOnly && <Badge variant="warn">Read-only</Badge>}
    </div>
  );
}
