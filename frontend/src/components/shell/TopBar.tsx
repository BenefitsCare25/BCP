import { useEffect } from "react";
import { usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { AccountMenu } from "./AccountMenu";

export function TopBar({ title }: { title: string }) {
  const { data: years = [], isSuccess } = usePolicyYears();
  const currentId = useSession((s) => s.currentPolicyYearId);
  const setPolicyYear = useSession((s) => s.setPolicyYear);

  // The per-page year picker was removed: the session policy year always tracks
  // the CURRENT (active) benefit year, and every page follows it. The
  // Configuration page owns read-only viewing of other years locally. Gated on
  // isSuccess so a still-loading list never clears a valid selection mid-fetch.
  useEffect(() => {
    if (!isSuccess) return;
    if (years.length === 0) {
      if (currentId !== null) setPolicyYear(null);
      return;
    }
    const active = years.find((y) => y.status === "active") ?? years[0];
    if (active && currentId !== active.id) setPolicyYear(active.id);
  }, [isSuccess, years, currentId, setPolicyYear]);

  return (
    <header className="h-14 border-b border-border bg-card px-6 flex items-center justify-between shrink-0">
      <h1 className="text-base font-semibold text-foreground">{title}</h1>
      <div className="flex items-center gap-3">
        <AccountMenu />
      </div>
    </header>
  );
}
