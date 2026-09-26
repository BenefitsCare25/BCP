import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { PencilLine } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  SECTION_LABEL,
  SETUP_PATH,
  setupSearch,
  type SetupSection,
} from "@/lib/setupLink";

interface Target {
  productCode: string;
  section?: SetupSection;
  categoryId?: string | null;
}

function targetLabel({ productCode, section, categoryId }: Target): string {
  if (categoryId && !section) return `Edit ${productCode} matching rule`;
  return `Edit ${productCode} ${section ? SECTION_LABEL[section].toLowerCase() : "setup"}`;
}

/** A pencil beside a derived value, opening the setup that produces it.
 *
 * Revealed on row hover and on keyboard focus so twenty of them don't sit on
 * a nine-row table permanently; the detail panel's `SourceLinks` strip carries
 * the same targets visibly for touch. Clicks never toggle the row it sits in.
 */
export function SourceIcon(target: Target) {
  const label = targetLabel(target);
  return (
    <Link
      to={SETUP_PATH}
      search={setupSearch(target.productCode, target)}
      onClick={(e) => e.stopPropagation()}
      aria-label={label}
      title={label}
      className="inline-flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 group-hover/row:opacity-100"
    >
      <PencilLine className="size-3" aria-hidden />
    </Link>
  );
}

/** A flag that is itself the way to fix what it flags. */
export function SourceFlag({
  children,
  tone,
  title,
  ...target
}: Target & { children: ReactNode; tone: "warn" | "muted"; title?: string }) {
  return (
    <Link
      to={SETUP_PATH}
      search={setupSearch(target.productCode, target)}
      onClick={(e) => e.stopPropagation()}
      title={title ?? targetLabel(target)}
      className={cn(
        "shrink-0 rounded underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        tone === "warn" ? "text-warn" : "text-muted-foreground",
      )}
    >
      {children}
    </Link>
  );
}

/** Every place this line's values are edited, as labelled links. */
export function SourceLinks({
  productCode,
  categoryId,
  hasSchedule,
}: {
  productCode: string;
  categoryId: string | null;
  hasSchedule: boolean;
}) {
  const sections: SetupSection[] = hasSchedule
    ? ["basis_of_cover", "schedule_of_benefits", "claim_limits", "header"]
    : ["basis_of_cover", "header"];
  const chip =
    "inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40";
  return (
    <nav aria-label={`Edit ${productCode} at source`} className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-2xs uppercase tracking-wider text-muted-foreground">
        Edit at source
      </span>
      {categoryId && (
        <Link
          to={SETUP_PATH}
          search={setupSearch(productCode, { categoryId })}
          className={chip}
        >
          <PencilLine className="size-3 text-muted-foreground" aria-hidden />
          Matching rule
        </Link>
      )}
      {sections.map((section) => (
        <Link
          key={section}
          to={SETUP_PATH}
          search={setupSearch(productCode, { section })}
          className={chip}
        >
          <PencilLine className="size-3 text-muted-foreground" aria-hidden />
          {SECTION_LABEL[section]}
        </Link>
      ))}
    </nav>
  );
}
