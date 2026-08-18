import type { ReactNode } from "react";
import { SectionLabel } from "@/components/ui/section-label";
import { InfoHint } from "@/components/ui/tooltip";

export function ReviewConfigEditorSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-1">
        <SectionLabel as="h3">{title}</SectionLabel>
        <InfoHint>{hint}</InfoHint>
      </div>
      {children}
    </section>
  );
}
