import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowRight, FileText, MapPin } from "lucide-react";
import {
  useCoverageOptions, usePortalCardArtwork, usePortalCards, usePortalClaims, usePortalDependants,
  usePortalEnrollment, usePortalMe, usePortalStatement, usePortalUtilization,
} from "@/api/portal";
import { usePortalConversations, type Conversation } from "@/api/portalMessages";
import type { MemberCard } from "@/api/panelCards";
import { isNotFoundError } from "@/lib/errors";
import { usePortalSession } from "@/stores/portalSession";
import { BenefitYearControl } from "../BenefitYearControl";
import { CardCanvas } from "../MemberCard";
import { PortalErrorState } from "../PortalErrorState";
import { holds } from "../capabilities";
import { buildCareRoutes } from "../leaf/careRoutes";
import { sectionArt } from "../leaf/careTone";
import { formatDay } from "../leaf/date";
import { isEmployeeLine } from "../memberVisibility";
import { useCompany } from "../useCompany";
import { familiarName, firstMemberId } from "../memberNames";
import { BenefitSheet } from "./BenefitSheet";
import { SkyStage, skyPeriod } from "./SkyStage";
import { TemporaryCard } from "./TemporaryCard";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good Morning" : hour < 18 ? "Good Afternoon" : "Good Evening";
}

function HeroCard({ card, companyName }: { card: MemberCard; companyName: string }) {
  const artwork = usePortalCardArtwork(card.card_id, "front", card.has_front);
  if (artwork.status === "absent") {
    return <TemporaryCard memberName={card.holder_name ?? ""} companyName={companyName} />;
  }
  return (
    <div className="portal-hero-card-wrap">
      <CardCanvas
        aspectRatio={card.aspect_ratio}
        artworkSrc={artwork.url}
        artworkStatus={artwork.status}
        fields={card.placements.fields.filter((field) => field.face === "front")}
        values={card.values}
        className="portal-hero-card"
        fallback="Your insurer hasn't uploaded a card design yet. Your card details remain available."
      />
    </div>
  );
}

function messageDestination(conversation: Conversation, company: string) {
  return conversation.subject.kind === "enquiry"
    ? { to: "/portal/$company/questions/$enquiryId" as const, params: { company, enquiryId: conversation.subject.id } }
    : { to: "/portal/$company/claims/$claimId" as const, params: { company, claimId: conversation.subject.id } };
}

/** Things only the member can move forward, in the order they would regret
 *  missing them. Empty = the strip does not render at all. */
function NeedsYou({ company }: { company: string }) {
  const me = usePortalMe();
  const claims = usePortalClaims();
  const enrollment = usePortalEnrollment();
  const waiting = (claims.data?.items ?? []).filter((claim) => claim.status === "needs_info" || claim.status === "draft");
  const window = me.data?.enrollment_open && holds(me.data.access.capabilities, "elect") ? enrollment.data?.window : null;
  if (waiting.length === 0 && !window) return null;
  return (
    <section className="clay-needs clay-rise clay-rise-2" aria-label="Needs your attention">
      {window && (
        <Link to="/portal/$company/enrollment" params={{ company }} className="clay-need tone-lime">
          <img src={sectionArt.enrol} alt="" />
          <span>
            <strong>Choose your benefits for next year</strong>
            <span>Your enrolment window closes {formatDay(window.closes_at)}</span>
          </span>
          <ArrowRight className="ml-auto size-5 shrink-0" aria-hidden />
        </Link>
      )}
      {waiting.slice(0, 3).map((claim) => (
        <Link key={claim.id} to="/portal/$company/claims/$claimId" params={{ company, claimId: claim.id }} className="clay-need tone-peach">
          <img src={sectionArt.claim} alt="" />
          <span>
            <strong>{claim.status === "draft" ? "Finish your claim" : "Your claim needs another document"}</strong>
            <span>{claim.provider_name || "Claim"} · visit on {formatDay(claim.incurred_date)}</span>
          </span>
          <ArrowRight className="ml-auto size-5 shrink-0" aria-hidden />
        </Link>
      ))}
    </section>
  );
}

export function MemberHome() {
  const company = useCompany();
  const navigate = useNavigate();
  const member = usePortalSession((state) => state.member);
  const me = usePortalMe();
  const cards = usePortalCards();
  const utilization = usePortalUtilization();
  const messages = usePortalConversations();
  const claims = usePortalClaims();
  const dependants = usePortalDependants();
  const statement = usePortalStatement();
  const options = useCoverageOptions();

  const canUseCard = holds(me.data?.access.capabilities, "entitlement");
  const canClaim = holds(me.data?.access.capabilities, "claim");
  const card = cards.data?.items.find((item) => item.holder_type === "employee" && item.has_front)
    ?? cards.data?.items.find((item) => item.holder_type === "employee")
    ?? null;
  const who = familiarName(member?.display_name || "");
  const companyLabel = me.data?.company?.legal_name || me.data?.company?.name || "";
  const coverage = (statement.data?.coverage ?? []).filter(isEmployeeLine);
  const routes = buildCareRoutes(coverage);
  const activeDependants = (dependants.data ?? []).filter((person) => person.status === "active");
  const claimCount = claims.data?.items.length ?? 0;
  const inbox = messages.data?.items ?? [];
  const year = me.data?.policy_year;
  const memberId = firstMemberId(options.data);
  // The notes state the member's own facts; each disappears rather than guess.
  const benefitsNote = routes.length > 0 ? `${routes.length} benefits, all yours.` : null;
  const familyNote = dependants.data
    ? activeDependants.length === 0 ? "Covered, start to finish." : `You + ${activeDependants.length} covered.`
    : null;

  return (
    <div className="clay-home">
      <SkyStage period={skyPeriod()} noteLeft={benefitsNote} noteRight={familyNote}>
        {(companyLabel || year) && (
          <p className="sky-eyebrow">
            {companyLabel}
            {companyLabel && year && <span aria-hidden> · </span>}
            {year && `${year.year} benefits`}
          </p>
        )}
        <h1>
          {greeting()}, <span className="name">{who}</span>
        </h1>
        <p className="sky-sub">Your cover, claims and panel card in one place.</p>
        <span className="sky-rule" aria-hidden />
        <div className="clay-hero-actions">
          {canClaim && (
            <Link className="clay-btn clay-btn-dark clay-btn-go" to="/portal/$company/claims/new" params={{ company }}>
              <FileText className="size-[18px]" aria-hidden /> Make a claim <ArrowRight className="clay-go size-4" aria-hidden />
            </Link>
          )}
          {canUseCard && (
            <Link className="clay-btn clay-btn-white clay-btn-go" to="/portal/$company/clinics" params={{ company }}>
              <MapPin className="size-[18px]" aria-hidden /> Find a clinic <ArrowRight className="clay-go size-4" aria-hidden />
            </Link>
          )}
        </div>
      </SkyStage>

      <NeedsYou company={company} />

      <div className="clay-section-head clay-rise clay-rise-2">
        <h2>Your benefits</h2>
        <div className="flex items-center gap-4">
          {year && (
            <span className="hidden lg:inline-flex">
              <BenefitYearControl start={year.start_date} end={year.end_date} />
            </span>
          )}
          <Link className="clay-section-link" to="/portal/$company/coverage" params={{ company }} search={{ tab: "usage" }}>
            What's left
          </Link>
        </div>
      </div>
      {statement.isError && !isNotFoundError(statement.error) ? (
        <PortalErrorState onRetry={() => { void statement.refetch(); }} />
      ) : statement.isLoading ? (
        <div className="clay-sheets" aria-busy="true">
          {[0, 1, 2, 3].map((n) => <div key={n} className="clay-sheet animate-pulse bg-shade" />)}
        </div>
      ) : routes.length === 0 ? (
        <div className="clay-panel">
          <p className="text-row text-label">Your benefits will appear here once your company's cover for the year goes live.</p>
        </div>
      ) : (
        <>
          <div className="clay-sheets clay-rise clay-rise-3">
            {routes.filter((route) => route.section === "care").map((route) => (
              <BenefitSheet key={route.key} route={route} utilization={utilization.data} to={{ tab: "benefits", p: route.key }} />
            ))}
          </div>
          {routes.some((route) => route.section === "other") && (
            <>
              <p className="clay-subhead">Also covered</p>
              <div className="clay-sheets clay-sheets-other clay-rise clay-rise-3">
                {routes.filter((route) => route.section === "other").map((route) => (
                  <BenefitSheet key={route.key} route={route} utilization={utilization.data} to={{ tab: "benefits", p: route.key }} />
                ))}
              </div>
            </>
          )}
        </>
      )}

      <div className="clay-row clay-row-3 clay-rise clay-rise-3">
        {canUseCard && (
          <section className="clay-panel clay-card-panel" aria-label="Your panel card">
            <div className="clay-panel-head">
              <h2>Your card</h2>
              <Link className="clay-section-link" to="/portal/$company/card" params={{ company }}>Show at clinic</Link>
            </div>
            <div className="clay-card-tilt">
              {cards.isLoading ? (
                <div className="aspect-[1.586] w-full animate-pulse rounded-[18px] bg-shade" aria-label="Loading your panel card" />
              ) : cards.isError && !isNotFoundError(cards.error) ? (
                <PortalErrorState onRetry={() => { void cards.refetch(); }} />
              ) : card?.has_front ? (
                <HeroCard card={card} companyName={companyLabel} />
              ) : (
                <TemporaryCard memberName={member?.display_name ?? ""} companyName={companyLabel} memberId={memberId} />
              )}
            </div>
          </section>
        )}
        <section className="clay-panel" aria-label="Messages">
          <div className="clay-panel-head">
            <h2>Messages</h2>
            <Link className="clay-section-link" to="/portal/$company/messages" params={{ company }}>View all</Link>
          </div>
          {messages.isLoading ? (
            <div className="h-16 animate-pulse rounded-2xl bg-shade" aria-busy="true" />
          ) : messages.isError && !isNotFoundError(messages.error) ? (
            <PortalErrorState onRetry={() => { void messages.refetch(); }} />
          ) : inbox.length === 0 ? (
            <div className="clay-empty">
              <img src={sectionArt.messages} alt="" />
              <span>
                <strong>No messages yet</strong>
                <span>Claim updates and replies land here.</span>
              </span>
            </div>
          ) : (
            <div>
              {inbox.slice(0, 3).map((conversation) => (
                <button
                  type="button"
                  key={`${conversation.subject.kind}-${conversation.subject.id}`}
                  className="clay-msg leaf-focus"
                  onClick={() => { void navigate(messageDestination(conversation, company)); }}
                >
                  <span className="min-w-0">
                    <strong>{conversation.subject.kind === "enquiry" ? conversation.subject.subject || "Your question" : conversation.subject.reference_no || "Claim update"}</strong>
                    <small>{conversation.last_message.body}</small>
                  </span>
                  {conversation.unread > 0 && <span className="clay-msg-unread">{conversation.unread} new</span>}
                </button>
              ))}
            </div>
          )}
        </section>

        <nav className="clay-quick" aria-label="Your account">
          <Link to="/portal/$company/coverage" params={{ company }} search={{ tab: "dependants" }} className="tone-blue">
            <img src={sectionArt.family} alt="" />
            <span>
              <small>Family</small>
              <strong>
                {dependants.isLoading ? "Loading" : dependants.isError && !isNotFoundError(dependants.error)
                  ? "Open family" : activeDependants.length === 0 ? "Just you" : `You + ${activeDependants.length}`}
              </strong>
            </span>
            <ArrowRight className="size-5" aria-hidden />
          </Link>
          <Link to="/portal/$company/claims" params={{ company }} className="tone-peach">
            <img src={sectionArt.claim} alt="" />
            <span>
              <small>Claims</small>
              <strong>
                {claims.isLoading ? "Loading" : claims.isError && !isNotFoundError(claims.error)
                  ? "Open claims" : claimCount === 0 ? "None yet" : `${claimCount} ${claimCount === 1 ? "claim" : "claims"}`}
              </strong>
            </span>
            <ArrowRight className="size-5" aria-hidden />
          </Link>
          {canUseCard && (
            <Link to="/portal/$company/clinics" params={{ company }} className="tone-mint">
              <img src={sectionArt.clinic} alt="" />
              <span>
                <small>Clinics</small>
                <strong>Find one near you</strong>
              </span>
              <ArrowRight className="size-5" aria-hidden />
            </Link>
          )}
        </nav>
      </div>

      <footer className="clay-footer">© {new Date().getFullYear()} Inspro Insurance Brokers</footer>
    </div>
  );
}
