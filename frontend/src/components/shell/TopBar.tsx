import { Calendar } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { useEffect } from "react";
import { formatPolicyRange } from "@/lib/policy-year";
import { AccountMenu } from "./AccountMenu";
import { ClientSwitcher } from "./ClientSwitcher";

export function TopBar({ title }: { title: string }) {
  const { data: years = [], isSuccess } = usePolicyYears();
  const currentId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);
  const firstId = years[0]?.id;

  // Reconcile the stored selection against the active client's year list. Gated
  // on isSuccess so a still-loading list (empty default) never clears a valid
  // selection mid-fetch. When the client has no years, drop any carried-over id
  // so pages keyed off currentPolicyYearId don't render a previous client's data.
  useEffect(() => {
    if (!isSuccess) return;
    if (years.length === 0) {
      if (currentId !== null) setPolicyYear(null);
      return;
    }
    if (!years.some((y) => y.id === currentId)) {
      setPolicyYear(firstId ?? null);
    }
  }, [isSuccess, years, firstId, currentId, setPolicyYear]);

  const current = years.find((y) => y.id === currentId) ?? years[0];

  return (
    <header className="h-14 border-b border-border bg-card px-6 flex items-center justify-between shrink-0">
      <h1 className="text-base font-semibold text-foreground">{title}</h1>
      <div className="flex items-center gap-3">
        <ClientSwitcher />
        <div className="flex items-center gap-2">
          <Calendar className="size-4 text-muted-foreground" />
          {years.length > 0 && current ? (
            <Select value={current.id} onValueChange={setPolicyYear}>
              <SelectTrigger className="h-8 min-w-[230px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {years.map((y) => (
                  <SelectItem key={y.id} value={y.id}>
                    {formatPolicyRange(y.coverage_start, y.coverage_end)} ·{" "}
                    {y.status}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-sm text-muted-foreground">No policy year</span>
          )}
        </div>
        <div className="h-5 w-px bg-border" />
        <AccountMenu />
      </div>
    </header>
  );
}
