import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { CareTone } from "./careTone";

/** A page with nothing in it yet, said once, warmly: a tone sheet, a title,
 *  one line of why, an optional next step, and the section's clay object.
 *  Every portal empty state uses this so none of them reads as an error. */
export function ClayEmpty({
  tone,
  art,
  title,
  children,
  action,
  className,
}: {
  tone: CareTone;
  art: string;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        `tone-${tone} relative min-h-44 overflow-hidden rounded-[28px] bg-[var(--tone-wash)] p-7 pr-36 sm:min-h-56 sm:p-9 sm:pr-64`,
        className,
      )}
    >
      <h2 className="text-2xl font-bold tracking-title text-record sm:text-3xl">{title}</h2>
      {children && <div className="mt-2 max-w-md text-md text-[var(--tone-ink)]">{children}</div>}
      {action && <div className="mt-6">{action}</div>}
      <img
        src={art}
        alt=""
        className="pointer-events-none absolute -bottom-3 right-1 size-32 object-contain sm:right-8 sm:size-52"
      />
    </section>
  );
}
