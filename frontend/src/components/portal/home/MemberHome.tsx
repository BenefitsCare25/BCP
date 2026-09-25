import { useNavigate, Link } from "@tanstack/react-router";
import {
  ArrowRight, ArrowUpRight, Bell, FileText, MapPin, MessageSquare, MessagesSquare,
  UsersRound,
} from "lucide-react";
import {
  usePortalCards, usePortalCardArtwork, usePortalClaims, usePortalDependants,
  usePortalMe, usePortalStatement, usePortalUtilization,
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
import { isEmployeeLine } from "../memberVisibility";
import { useCompany } from "../useCompany";
import { HomeLimits } from "./HomeLimits";
import { TemporaryCard } from "./TemporaryCard";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function familiarName(displayName: string): string {
  const preferred = displayName.match(/\(([^)]+)\)/)?.[1]?.trim();
  return preferred || displayName.trim().split(/\s+/)[0] || "there";
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

  const canUseCard = holds(me.data?.access.capabilities, "entitlement");
  const canClaim = holds(me.data?.access.capabilities, "claim");
  const card = cards.data?.items.find((item) => item.holder_type === "employee" && item.has_front)
    ?? cards.data?.items.find((item) => item.holder_type === "employee")
    ?? null;
  const who = familiarName(member?.display_name || member?.email || "");
  const companyLabel = me.data?.company?.legal_name || me.data?.company?.name || "";
  const coverage = (statement.data?.coverage ?? []).filter(isEmployeeLine);
  const care = buildCareRoutes(coverage).filter((route) => route.section === "care").slice(0, 4);
  const activeDependants = (dependants.data ?? []).filter((person) => person.status === "active");
  const claimCount = claims.data?.items.length ?? 0;
  const inbox = messages.data?.items ?? [];
  return (
    <div className="portal-home">
      <div className="portal-home-stage">
      <section className="portal-hero" aria-label="Welcome">
        <div className="portal-hero-copy">
          {companyLabel && <p className="portal-company-name">{companyLabel}</p>}
          <h1>{greeting()},<br />{who}.</h1>
          <p className="portal-hero-subtitle">What would you like to do today?</p>
          <div className="portal-hero-actions">
            {canClaim && <Link className="portal-action-primary" to="/portal/$company/claims/new" params={{ company }}>
              <FileText size={20} aria-hidden /> Make a claim <ArrowRight size={17} aria-hidden />
            </Link>}
            {canUseCard && (
              <Link className="portal-action-secondary" to="/portal/$company/clinics" params={{ company }}>
                <MapPin size={20} aria-hidden /> Find a clinic
              </Link>
            )}
          </div>
        </div>
        <div className="portal-hero-aside">
          {me.data?.policy_year && <BenefitYearControl start={me.data.policy_year.start_date} end={me.data.policy_year.end_date} className="portal-year" />}
          {canUseCard && (
            <div className="portal-hero-card-area">
              {cards.isLoading ? <div className="portal-card-skeleton" aria-label="Loading your panel card" />
                : cards.isError && !isNotFoundError(cards.error) ? <PortalErrorState onRetry={() => { void cards.refetch(); }} />
                : card?.has_front ? <HeroCard card={card} companyName={companyLabel} />
                : <TemporaryCard memberName={member?.display_name ?? ""} companyName={companyLabel} />}
              <Link className="portal-card-link" to="/portal/$company/card" params={{ company }}>View panel card <ArrowUpRight size={17} aria-hidden /></Link>
            </div>
          )}
        </div>
      </section>

      <div className="portal-home-panels">
        <section className="portal-glass portal-messages" aria-label="Messages">
          <div className="portal-panel-head">
            <span className="portal-panel-title"><MessageSquare size={23} strokeWidth={1.4} aria-hidden /> Messages</span>
            <Link className="portal-text-link" to="/portal/$company/messages" params={{ company }}>View all <ArrowUpRight size={17} aria-hidden /></Link>
          </div>
          {messages.isLoading ? <div className="portal-panel-loading" aria-busy="true" />
            : messages.isError && !isNotFoundError(messages.error) ? <PortalErrorState onRetry={() => { void messages.refetch(); }} />
            : inbox.length === 0 ? <div className="portal-message-empty"><MessagesSquare size={66} strokeWidth={1.15} aria-hidden /><p>No messages yet</p><span>Claim updates and replies will appear here.</span></div>
            : <div className="portal-message-list">
              {inbox.slice(0, 3).map((conversation) => (
                <button
                  type="button"
                  key={`${conversation.subject.kind}-${conversation.subject.id}`}
                  className="portal-message-row"
                  onClick={() => { void navigate(messageDestination(conversation, company)); }}
                >
                  <span className="portal-message-dot"><Bell size={16} aria-hidden /></span>
                  <span><strong>{conversation.subject.kind === "enquiry" ? conversation.subject.subject || "Your enquiry" : conversation.subject.reference_no || "Claim update"}</strong><small>{conversation.last_message.body}</small></span>
                  {conversation.unread > 0 && <span className="portal-message-unread">{conversation.unread} unread</span>}
                  <ArrowRight size={18} aria-hidden />
                </button>
              ))}
            </div>}
        </section>
        <HomeLimits
          company={company}
          utilization={utilization.data}
          coverage={coverage}
          careRoutes={care}
          isLoading={utilization.isLoading}
          error={utilization.isError ? utilization.error : null}
          onRetry={() => { void utilization.refetch(); }}
        />
      </div>
      </div>

      <section className="portal-glass portal-quick" aria-label="Your account">
        <Link to="/portal/$company/coverage" params={{ company }} search={{ tab: "dependants" }} className="portal-quick-item">
          <UsersRound size={31} strokeWidth={1.4} aria-hidden /><span><small>Family</small><strong>{dependants.isLoading ? "Loading" : dependants.isError && !isNotFoundError(dependants.error) ? "Open family" : `${1 + activeDependants.length} covered`}</strong></span><ArrowRight size={19} aria-hidden />
        </Link>
        <Link to="/portal/$company/claims" params={{ company }} className="portal-quick-item">
          <FileText size={31} strokeWidth={1.4} aria-hidden /><span><small>Claims</small><strong>{claims.isLoading ? "Loading" : claims.isError && !isNotFoundError(claims.error) ? "Open claims" : claimCount === 0 ? "No claims yet" : `${claimCount} ${claimCount === 1 ? "claim" : "claims"}`}</strong></span><ArrowRight size={19} aria-hidden />
        </Link>
        {canUseCard && <Link to="/portal/$company/clinics" params={{ company }} className="portal-quick-item"><MapPin size={31} strokeWidth={1.4} aria-hidden /><span><small>Clinics</small><strong>Find a clinic</strong></span><ArrowRight size={19} aria-hidden /></Link>}
      </section>
      {me.data?.enrollment_open && holds(me.data.access.capabilities, "elect") && (
        <Link className="portal-enrol-notice" to="/portal/$company/enrollment" params={{ company }}>Your enrolment window is open <ArrowRight size={17} aria-hidden /></Link>
      )}
      <footer className="portal-home-footer">© {new Date().getFullYear()} Inspro Insurance Brokers.</footer>
    </div>
  );
}
