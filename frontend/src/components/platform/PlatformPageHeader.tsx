import type { ReactNode } from "react";

/** Title row of a platform console page, matching the broker settings pages:
 *  heading and one-line purpose on the left, the page's primary action on the
 *  right. */
export function PlatformPageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        {eyebrow}
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        <div className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</div>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
