import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  message: string;
  reloading: boolean;
  onReload: () => void;
}

export function SetupConflictAlert({ message, reloading, onReload }: Props) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-lg border border-error/30 bg-error-soft px-3 py-2.5 text-sm text-error sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex items-start gap-2">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div>
          <p className="font-medium">{message}</p>
          <p className="mt-1 text-foreground">
            Reloading replaces this form with the saved server version.
          </p>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        loading={reloading}
        onClick={onReload}
      >
        <RefreshCw className="size-4" aria-hidden="true" />
        Reload latest
      </Button>
    </div>
  );
}
