/** Member e-cards — the digital panel cards to show at a clinic counter.
 * Mirrored read-only in the broker employee-view preview. */
import { Loader2 } from "lucide-react";
import { usePortalCardArtwork, usePortalCards } from "@/api/portal";
import { MemberCardList } from "@/components/portal/MemberCard";
import { PortalErrorState } from "@/components/portal/PortalErrorState";

export function PortalCardPage() {
  const { data, isLoading, error, refetch } = usePortalCards();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-foreground">My card</h1>
        <p className="text-sm text-muted-foreground">
          Show this at a panel clinic. One card per plan you're covered under,
          plus a card for each covered family member.
        </p>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading your cards…
        </div>
      ) : error ? (
        <PortalErrorState onRetry={() => void refetch()} />
      ) : (
        <MemberCardList
          cards={data?.items ?? []}
          useArtwork={usePortalCardArtwork}
        />
      )}
    </div>
  );
}
