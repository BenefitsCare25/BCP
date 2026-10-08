import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatError } from "@/lib/errors";

/** Loading line for a card's list. */
export function ListLoading({ label }: { label: string }) {
  return (
    <p role="status" className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden="true" /> {label}
    </p>
  );
}

/** A failed list read with its reason and a retry, in place of the list. */
export function ListError({
  what,
  error,
  onRetry,
}: {
  what: string;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="space-y-2 py-2 text-sm">
      <p>
        Could not load {what}. {formatError(error)}
      </p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
