import { Info } from "lucide-react";

interface GuideItem {
  label: string;
  description: string;
}

interface PageGuideProps {
  purpose: string;
  connections: GuideItem[];
}

export function PageGuide({ purpose, connections }: PageGuideProps) {
  return (
    <div className="rounded-lg border border-border bg-muted/40 p-4 space-y-3">
      <div className="flex items-start gap-2">
        <Info className="size-4 text-muted-foreground mt-0.5 shrink-0" />
        <p className="text-sm text-muted-foreground leading-relaxed">
          {purpose}
        </p>
      </div>
      {connections.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {connections.map((c) => (
            <div key={c.label} className="text-xs space-y-0.5">
              <span className="font-medium text-foreground/80">{c.label}</span>
              <p className="text-muted-foreground">{c.description}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
