import { useEffect } from "react";
import { Menu } from "lucide-react";
import { usePolicyYears } from "@/api/hooks";
import { useSession } from "@/stores/session";
import { AccountMenu } from "./AccountMenu";

export function TopBar({
  title,
  onMenuClick,
}: {
  title: string;
  onMenuClick?: () => void;
}) {
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
    <header className="h-14 border-b border-border bg-card px-4 sm:px-6 flex items-center justify-between gap-2 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        {onMenuClick && (
          <button
            type="button"
            onClick={onMenuClick}
            aria-label="Open navigation menu"
            className="lg:hidden flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <Menu className="size-5" />
          </button>
        )}
        <h1 className="truncate text-base font-semibold text-foreground">{title}</h1>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <AccountMenu />
      </div>
    </header>
  );
}
