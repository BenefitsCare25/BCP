import { useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowRight, TriangleAlert } from "lucide-react";
import { useCoverageLimits } from "@/api/coverageLimits";
import { useSession } from "@/stores/session";
import { LIMIT_KIND, LIMIT_ORDER } from "./limitKinds";

const SEEN_KEY = "inspro.coverageLimitNoticesSeen";

function readSeen(): string | null {
  try {
    return window.sessionStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

function writeSeen(value: string): void {
  try {
    window.sessionStorage.setItem(SEEN_KEY, value);
  } catch {
    // Storage blocked (private mode): the badge simply re-appears next load.
  }
}

/**
 * Slip-limit crossings for the selected company and benefit year, as the
 * broker bell's persistent section. They stay listed until resolved — the
 * crossing is a fact about the roster, not a message to dismiss — but count as
 * unread only until the bell is opened on that exact set of counts.
 */
export function useLimitNotices(): {
  content: ReactNode;
  unread: number;
  acknowledge: () => void;
} {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const { data } = useCoverageLimits(policyYearId);
  const [seen, setSeen] = useState(readSeen);
  const counts = data?.counts ?? {};
  // A cap is the slip working as intended; only crossings needing action notify.
  const kinds = LIMIT_ORDER.filter((kind) => kind !== "capped" && (counts[kind] ?? 0) > 0);
  const signature = `${policyYearId}:${kinds.map((k) => `${k}=${counts[k]}`).join(",")}`;

  const acknowledge = () => {
    if (!kinds.length || seen === signature) return;
    setSeen(signature);
    writeSeen(signature);
  };

  const content = kinds.length ? (
    <div className="border-b border-border px-3 py-2.5">
      <p className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
        Coverage limits
      </p>
      <ul className="mt-1.5 grid gap-1.5">
        {kinds.map((kind) => (
          <li key={kind} className="flex items-start gap-2 text-sm text-foreground">
            <TriangleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warn" />
            {LIMIT_KIND[kind].summary(counts[kind] ?? 0)}
          </li>
        ))}
      </ul>
      <Link
        to="/policy-admin/member-coverage"
        search={{ show: "limits" }}
        className="mt-2 inline-flex items-center gap-1 rounded text-xs font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        Review in Member Coverage <ArrowRight aria-hidden className="size-3.5" />
      </Link>
    </div>
  ) : null;

  return {
    content,
    unread: kinds.length && seen !== signature ? kinds.length : 0,
    acknowledge,
  };
}
