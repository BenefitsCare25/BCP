import { usePortalTranslation } from "@/i18n/portal";
import { Button } from "@/components/ui/button";

interface PaginationControlsProps {
  page: number;
  pages: number;
  onPageChange: (next: number) => void;
}

export function PaginationControls({
  page,
  pages,
  onPageChange,
}: PaginationControlsProps) {
  const pt = usePortalTranslation();
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-between pt-3">
      <div className="text-xs text-muted-foreground">
        {pt("Page")} {page + 1}  {pt("of")} {pages}
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => onPageChange(Math.max(0, page - 1))}
          disabled={page === 0}
        >
          {pt("Previous")} </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onPageChange(Math.min(pages - 1, page + 1))}
          disabled={page >= pages - 1}
        >
          {pt("Next")} </Button>
      </div>
    </div>
  );
}
