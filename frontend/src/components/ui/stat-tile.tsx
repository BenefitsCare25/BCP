import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/cn";

type Variant = "good" | "warn" | "error";

const TONES: Record<Variant, string> = {
  good: "text-good",
  warn: "text-warn",
  error: "text-error",
};

interface Props {
  label: string;
  value: number | string;
  variant?: Variant;
  formatNumber?: boolean;
}

export function StatTile({ label, value, variant, formatNumber = false }: Props) {
  const display =
    formatNumber && typeof value === "number" ? value.toLocaleString() : value;
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div
          className={cn(
            "text-2xl font-semibold mt-1",
            variant ? TONES[variant] : "text-foreground",
          )}
        >
          {display}
        </div>
      </CardContent>
    </Card>
  );
}
