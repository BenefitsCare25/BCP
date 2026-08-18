import { Eye, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ReviewPromptPreview({
  prompt,
  pending,
  onPreview,
}: {
  prompt: string | null;
  pending: boolean;
  onPreview: () => void;
}) {
  return (
    <section className="space-y-2">
      <Button type="button" variant="outline" size="sm" disabled={pending} onClick={onPreview}>
        {pending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Eye className="size-3.5" />
        )}
        <span className="ml-1.5">Preview AI prompt</span>
      </Button>
      {prompt && (
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted p-3 text-2xs leading-relaxed text-foreground whitespace-pre-wrap">
          {prompt}
        </pre>
      )}
    </section>
  );
}
