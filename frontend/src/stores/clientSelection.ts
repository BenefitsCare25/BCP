/** Recovery from a company selection the server will not act on.
 *
 * The broker API answers a write with 409 `client_selection_stale` when the
 * `X-Inspro-Client` company is no longer one this user may act on, and
 * `client_selection_required` when a write needs a company and none was sent.
 * Either way the write did not happen. The stored company and benefit year are
 * dropped with every cached query, and the shell asks for a fresh choice — the
 * write is never replayed, because the user must see which company it would
 * now land on. */
import { create } from "zustand";
import { toast } from "sonner";
import { fetchMe, meQueryKey } from "@/api/me";
import { queryClient } from "@/lib/queryClient";
import { useSession } from "@/stores/session";

export const CLIENT_SELECTION_CODES: ReadonlySet<string> = new Set([
  "client_selection_stale",
  "client_selection_required",
]);

const STALE_NOTICE =
  "Your company selection is out of date, so that change was not saved. Choose the company again, then retry.";
const REQUIRED_NOTICE =
  "Choose a company before making changes. That change was not saved.";
const READ_NOTICE = "This page needs a company. Choose one to continue.";

/** Why the company picker is open, after a refused write. Cleared by a pick. */
export const useClientSelectionNotice = create<{
  notice: string | null;
  clear: () => void;
}>((set) => ({ notice: null, clear: () => set({ notice: null }) }));

/** Drop the stored company, its benefit year and every cached query, so no
 *  page keeps rendering data fetched under the old selection. */
export function resetClientSelection(): void {
  useSession.getState().setActiveClient(null);
  void queryClient.cancelQueries();
  queryClient.removeQueries();
}

/** Called by the API client on a 409 selection refusal from any broker call.
 *
 * `read` marks a refused GET. The server answers reads with a fallback company,
 * so a read is refused only when it has none to fall back on — a platform
 * admin while no company exists at all. With nothing selected there is no
 * selection or company data to drop, and wiping the cache anyway remounted
 * every query straight into the same refusal: an endless fetch loop. The shell
 * explains that state itself (the picker, or its no-company notice), so a read
 * raises no toast either. */
export function recoverClientSelection(
  code: string,
  { read = false }: { read?: boolean } = {},
): void {
  if (!read || useSession.getState().activeClientId != null) {
    resetClientSelection();
    // The picker lists the companies /me returns, so load them now rather than
    // waiting for a component to notice the cache is empty.
    void queryClient.prefetchQuery({
      queryKey: meQueryKey(null),
      queryFn: fetchMe,
      staleTime: 60_000,
    });
  }
  if (read) {
    // Never over a refused write's notice: that one says a change was lost.
    if (useClientSelectionNotice.getState().notice == null) {
      useClientSelectionNotice.setState({ notice: READ_NOTICE });
    }
    return;
  }
  const notice =
    code === "client_selection_required" ? REQUIRED_NOTICE : STALE_NOTICE;
  useClientSelectionNotice.setState({ notice });
  // One toast however many in-flight writes were refused together.
  toast.warning(notice, { id: "client-selection" });
}
