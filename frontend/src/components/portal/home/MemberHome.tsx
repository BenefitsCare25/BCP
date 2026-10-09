import { usePortalTranslation } from "@/i18n/portal";
import { systemClaimBody } from "@/i18n/systemMessages";
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
import { useBrandOwner } from "@/components/brand/BrandProvider";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good Morning" : hour < 18 ? "Good Afternoon" : "Good Evening";
}

function HeroCard({ card, companyName }: { card: MemberCard; companyName: string }) {
  const pt = usePortalTranslation();
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
        fallback={pt("Your insurer hasn't uploaded a card design yet. Your card details remain available.")}
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
  const pt = usePortalTranslation();
  const me = usePortalMe();
  const claims = usePortalClaims();
  const enrollment = usePortalEnrollment();
  const waiting = (claims.data?.items ?? []).filter((claim) => claim.status === "needs_info" || claim.status === "draft");
  const window = me.data?.enrollment_open && holds(me.data.access.capabilities, "elect") ? enrollment.data?.window : null;
  if (waiting.length === 0 && !window) return null;
  return (
    <section className="clay-needs clay-rise clay-rise-2" aria-label={pt("Needs your attention")}>
      {window && (
        <Link to="/portal/$company/enrollment" params={{ company }} className="clay-need tone-lime">
          <img src={sectionArt.enrol} alt="" />
          <span>
            <strong>{pt("Choose your benefits for next year")}</strong>
            <span>{pt("Your enrolment window closes")} {formatDay(window.closes_at)}</span>
          </span>
          <ArrowRight className="ml-auto size-5 shrink-0" aria-hidden />
        </Link>
      )}
      {waiting.slice(0, 3).map((claim) => (
        <Link key={claim.id} to="/portal/$company/claims/$claimId" params={{ company, claimId: claim.id }} className="clay-need tone-peach">
          <img src={sectionArt.claim} alt="" />
          <span>
            <strong>{claim.status === "draft" ? pt("Finish your claim") : pt("Your claim needs another document")}</strong>
            <span>{claim.provider_name || pt("Claim")}  {pt("· visit on")} {formatDay(claim.incurred_date)}</span>
          </span>
          <ArrowRight className="ml-auto size-5 shrink-0" aria-hidden />
        </Link>
      ))}
    </section>
  );
}

export function MemberHome() {
  const pt = usePortalTranslation();
  const owner = useBrandOwner();
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
  const benefitsNote = routes.length > 0 ? pt("{0} benefits, all yours.", [routes.length]) : null;
  const familyNote = dependants.data
    ? activeDependants.length === 0 ? pt("Covered, start to finish.") : pt("You + {0} covered.", [activeDependants.length])
    : null;

  return (
    <div className="clay-home">
      <SkyStage period={skyPeriod()} noteLeft={benefitsNote} noteRight={familyNote}>
        {(companyLabel || year) && (
          <p className="sky-eyebrow">
            {companyLabel}
            {companyLabel && year && <span aria-hidden> · </span>}
            {year && pt("{0} benefits", [year.year])}
          </p>
        )}
        <h1>
          {pt(greeting())}, <span className="name">{who}</span>
        </h1>
        <p className="sky-sub">{pt("Your cover, claims and panel card in one place.")}</p>
        <span className="sky-rule" aria-hidden />
        <div className="clay-hero-actions">
          {canClaim && (
            <Link className="clay-btn clay-btn-dark clay-btn-go" to="/portal/$company/claims/new" params={{ company }}>
              <FileText className="size-[18px]" aria-hidden />  {pt("Make a claim")} <ArrowRight className="clay-go size-4" aria-hidden />
            </Link>
          )}
          {canUseCard && (
            <Link className="clay-btn clay-btn-white clay-btn-go" to="/portal/$company/clinics" params={{ company }}>
              <MapPin className="size-[18px]" aria-hidden />  {pt("Find a clinic")} <ArrowRight className="clay-go size-4" aria-hidden />
            </Link>
          )}
        </div>
      </SkyStage>

      <NeedsYou company={company} />

      <div className="clay-section-head clay-rise clay-rise-2">
        <h2>{pt("Your benefits")}</h2>
        <div className="flex items-center gap-4">
          {year && (
            <span className="hidden lg:inline-flex">
              <BenefitYearControl start={year.start_date} end={year.end_date} />
            </span>
          )}
          <Link className="clay-section-link" to="/portal/$company/coverage" params={{ company }} search={{ tab: "usage" }}>
            {pt("What's left")} </Link>
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
          <p className="text-row text-label">{pt("Your benefits will appear here once your company's cover for the year goes live.")}</p>
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
              <p className="clay-subhead">{pt("Also covered")}</p>
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
          <section className="clay-panel clay-card-panel" aria-label={pt("Your panel card")}>
            <div className="clay-panel-head">
              <h2>{pt("Your card")}</h2>
              <Link className="clay-section-link" to="/portal/$company/card" params={{ company }}>{pt("Show at clinic")}</Link>
            </div>
            <div className="clay-card-tilt">
              {cards.isLoading ? (
                <div className="aspect-[1.586] w-full animate-pulse rounded-[18px] bg-shade" aria-label={pt("Loading your panel card")} />
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
        <section className="clay-panel" aria-label={pt("Messages")}>
          <div className="clay-panel-head">
            <h2>{pt("Messages")}</h2>
            <Link className="clay-section-link" to="/portal/$company/messages" params={{ company }}>{pt("View all")}</Link>
          </div>
          {messages.isLoading ? (
            <div className="h-16 animate-pulse rounded-2xl bg-shade" aria-busy="true" />
          ) : messages.isError && !isNotFoundError(messages.error) ? (
            <PortalErrorState onRetry={() => { void messages.refetch(); }} />
          ) : inbox.length === 0 ? (
            <div className="clay-empty">
              <img src={sectionArt.messages} alt="" />
              <span>
                <strong>{pt("No messages yet")}</strong>
                <span>{pt("Claim updates and replies land here.")}</span>
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
                    <strong>{conversation.subject.kind === "enquiry" ? conversation.subject.subject || pt("Your question") : conversation.subject.reference_no || pt("Claim update")}</strong>
                    <small>{systemClaimBody(conversation.last_message, pt)}</small>
                  </span>
                  {conversation.unread > 0 && <span className="clay-msg-unread">{conversation.unread}  {pt("new")}</span>}
                </button>
              ))}
            </div>
          )}
        </section>

        <nav className="clay-quick" aria-label={pt("Your account")}>
          <Link to="/portal/$company/coverage" params={{ company }} search={{ tab: "dependants" }} className="tone-blue">
            <img src={sectionArt.family} alt="" />
            <span>
              <small>{pt("Family")}</small>
              <strong>
                {dependants.isLoading ? pt("Loading") : dependants.isError && !isNotFoundError(dependants.error)
                  ? pt("Open family") : activeDependants.length === 0 ? pt("Just you") : pt("You + {0}", [activeDependants.length])}
              </strong>
            </span>
            <ArrowRight className="size-5" aria-hidden />
          </Link>
          <Link to="/portal/$company/claims" params={{ company }} className="tone-peach">
            <img src={sectionArt.claim} alt="" />
            <span>
              <small>{pt("Claims")}</small>
              <strong>
                {claims.isLoading ? pt("Loading") : claims.isError && !isNotFoundError(claims.error)
                  ? pt("Open claims") : claimCount === 0 ? pt("None yet") : pt("{0} {1}", [claimCount, pt(claimCount === 1 ? "claim" : "claims")])}
              </strong>
            </span>
            <ArrowRight className="size-5" aria-hidden />
          </Link>
          {canUseCard && (
            <Link to="/portal/$company/clinics" params={{ company }} className="tone-mint">
              <img src={sectionArt.clinic} alt="" />
              <span>
                <small>{pt("Clinics")}</small>
                <strong>{pt("Find one near you")}</strong>
              </span>
              <ArrowRight className="size-5" aria-hidden />
            </Link>
          )}
        </nav>
      </div>

      <footer className="clay-footer">© {new Date().getFullYear()} {owner}</footer>
    </div>
  );
}
