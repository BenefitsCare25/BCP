import { useEffect } from "react";

const SUFFIX = "My Benefits";

/** The bare title shown when no page names itself: the brand's product name.
 *  The server already injects it into `index.html`; `setBaseDocumentTitle`
 *  keeps it right when the brand read in the browser differs (a company's
 *  override on the portals). */
let baseTitle: string | null = null;
let pageTitles = 0;

export function setBaseDocumentTitle(title: string): void {
  baseTitle = title;
  if (pageTitles === 0) document.title = title;
}

/**
 * Names the page in the browser's title bar, history and tab list.
 *
 * The document title used to be static across every member route ("Inspro Spike
 * — Placement Slip Preview", left over from a prototype), which fails WCAG 2.4.2
 * and makes a member's open tabs and back-history indistinguishable from one
 * another.
 *
 * Wired into every routed `/portal/*` page. The coverage sub-tabs (benefits /
 * utilization / dependants) deliberately do NOT call it — they are not routed
 * on their own, and `coverage.tsx` sets the title per tab; a second call would
 * race the parent's. A new portal ROUTE that omits this inherits the bare
 * brand title and silently reopens the failure.
 */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    pageTitles += 1;
    document.title = `${title} · ${SUFFIX}`;
    return () => {
      pageTitles -= 1;
      document.title = pageTitles === 0 && baseTitle ? baseTitle : previous;
    };
  }, [title]);
}
