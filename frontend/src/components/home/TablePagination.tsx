import { useState } from "react";
import { Button } from "@/components/ui/button";

export function useTablePage<T>(rows: T[], size = 20) {
  const [requestedPage, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.min(requestedPage, pages - 1);
  return { rows: rows.slice(page * size, (page + 1) * size), page, pages, setPage, total: rows.length, size };
}

export function TablePagination({ page, pages, setPage, total, size }: {
  page: number; pages: number; setPage: (page: number) => void; total: number; size: number;
}) {
  if (pages <= 1) return null;
  return <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
    <span>{page * size + 1}–{Math.min((page + 1) * size, total)} of {total}</span>
    <div className="flex gap-2">
      <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
      <Button variant="outline" size="sm" disabled={page + 1 === pages} onClick={() => setPage(page + 1)}>Next</Button>
    </div>
  </div>;
}
