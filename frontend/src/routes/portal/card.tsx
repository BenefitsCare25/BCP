/** "My card" — the digital panel cards a member shows at a clinic counter.
 *
 * No page heading and no lede: the shell carries the h1, the nav already says
 * which section this is, and "show this at a panel clinic" is what the card
 * itself looks like. Every word that survives here is one a member could not
 * work out from the screen. */
import { Link } from "@tanstack/react-router";
import { MapPin } from "lucide-react";
import { useCoverageOptions, usePortalCardArtwork, usePortalCards, usePortalMe } from "@/api/portal";
import { TemporaryCard } from "@/components/portal/home/TemporaryCard";
import { firstMemberId } from "@/components/portal/memberNames";
import { actionClass } from "@/components/portal/leaf/Action";
import { useCompany } from "@/components/portal/useCompany";
import { usePortalSession } from "@/stores/portalSession";
import { CardLeaf } from "@/components/portal/leaf/CardLeaf";
import { LeafSkeleton } from "@/components/portal/leaf/LeafSkeleton";
import { Mount } from "@/components/portal/leaf/Mount";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { isNotFoundError } from "@/lib/errors";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function PortalCardPage() {
  useDocumentTitle("My card");
  const { data, isLoading, isError, error, refetch } = usePortalCards();

  if (isLoading) return <LeafSkeleton label="Loading your cards" mounts={1} />;

  // Anything but a 404 is a fetch that failed, and must not read as "your
  // company issued no card".
  if (isError && !isNotFoundError(error)) {
    return <PortalErrorState onRetry={() => void refetch()} />;
  }

  // **A 404 here is NOT "no card yet" — it is "no active coverage".** It is the
  // only 404 this endpoint raises (`resolve_member_employee`), and it fires
  // whenever no benefit year is flagged current, which per CLAUDE.md is the
  // DEFAULT state of an otherwise fully configured company. Falling through to
  // `CardLeaf`'s empty state told the member "your HR team adds your panel card
  // once your plan is set up with the insurer" — a confident, wrong diagnosis
  // of a one-click broker fix, and a different answer from the one the broker's
  // own preview gives for the identical state. The wording matches the other
  // member surfaces' no-coverage copy (`routes/portal/benefits`), so the whole
  // portal says one thing when a company's year is not current.
  if (isError || !data) {
    return (
      <Mount label="No coverage on record">
        <p className="text-row text-label">
          We don&rsquo;t have any cover recorded against your name for this
          period, so there&rsquo;s no card to show yet. This usually means your
          company&rsquo;s cover for the year hasn&rsquo;t been finalised. Your
          HR team can tell you where things stand.
        </p>
      </Mount>
    );
  }

  if (data.items.length === 0) return <NoIssuedCard />;
  return <CardLeaf cards={data.items} useArtwork={usePortalCardArtwork} />;
}

/** No insurer card yet — but the member usually already HAS the one thing a
 *  clinic counter asks for: their insurer member ID, served on the claim
 *  options. So the page shows the preview card carrying it, instead of
 *  sending them to ask HR for a number the portal already knows. */
function NoIssuedCard() {
  const company = useCompany();
  const member = usePortalSession((state) => state.member);
  const me = usePortalMe();
  const options = useCoverageOptions();
  const memberId = firstMemberId(options.data);
  const companyLabel = me.data?.company?.legal_name || me.data?.company?.name || "";
  return (
    <div className="mx-auto grid max-w-4xl items-center gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="clay-card-tilt mx-auto w-full max-w-[400px]">
        <TemporaryCard memberName={member?.display_name ?? ""} companyName={companyLabel} memberId={memberId} />
      </div>
      <div>
        <h2 className="text-3xl font-bold tracking-title text-record">
          {memberId ? "Show your member ID at the clinic" : "Your card is on its way"}
        </h2>
        <p className="mt-3 text-md text-label">
          {memberId
            ? `Your insurer hasn't sent the card design yet. Quote your ${memberId.insurer ?? "insurer"} member ID ${memberId.id} at any panel clinic — it's what the card would show.`
            : "Your insurer hasn't issued your card yet. It will appear here as soon as it does."}
        </p>
        <Link to="/portal/$company/clinics" params={{ company }} className={actionClass("primary", { className: "mt-6" })}>
          <MapPin className="size-4" aria-hidden /> Find a panel clinic
        </Link>
      </div>
    </div>
  );
}
