import { useNavigate, Link } from "@tanstack/react-router";
import {
  ArrowRight, ArrowUpRight, Bell, FileText, MapPin, MessageSquare,
  ShieldCheck, Stethoscope, UserRound, UsersRound, Building2,
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

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function familiarName(displayName: string): string {
  const preferred = displayName.match(/\(([^)]+)\)/)?.[1]?.trim();
  return preferred || displayName.trim().split(/\s+/)[0] || "there";
}

function ToothIcon() {
  return (
    <svg width="29" height="29" viewBox="0 0 29 29" fill="none" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14.5 6.3c-2.5-1.4-4.1-1.7-6-1.1-3 1-3.7 3.3-3.2 6.1.4 2 1.7 4 2.2 7.7.3 2.2.7 4.7 2.3 4.8 1.9.1 2.1-2.6 2.9-4.8.6-1.8 1-2.8 2.1-2.8s1.5 1 2.1 2.8c.8 2.2 1 4.9 2.9 4.8 1.6-.1 2-2.6 2.3-4.8.5-3.7 1.8-5.7 2.2-7.7.5-2.8-.2-5.1-3.2-6.1-1.9-.6-3.5-.3-6 1.1Z" />
    </svg>
  );
}

function HeroCard({ card }: { card: MemberCard }) {
  const artwork = usePortalCardArtwork(card.card_id, "front", card.has_front);
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
  const care = buildCareRoutes((statement.data?.coverage ?? []).filter(isEmployeeLine)).filter((route) => route.section === "care").slice(0, 4);
  const activeDependants = (dependants.data ?? []).filter((person) => person.status === "active");
  const claimCount = claims.data?.items.length ?? 0;
  const inbox = messages.data?.items ?? [];
  const unread = messages.data?.unread_total ?? 0;

  return (
    <div className="portal-home">
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
          <Link className="portal-hero-message" to="/portal/$company/messages" params={{ company }}>
            <MessageSquare size={17} aria-hidden />
            {messages.isLoading ? "Loading messages" : messages.isError && !isNotFoundError(messages.error)
              ? "Open messages" : unread > 0 ? `${unread} unread ${unread === 1 ? "message" : "messages"}` : "No new messages"}
          </Link>
        </div>
        <div className="portal-hero-aside">
          {me.data?.policy_year && <BenefitYearControl start={me.data.policy_year.start_date} end={me.data.policy_year.end_date} className="portal-year" />}
          {canUseCard && (
            <div className="portal-hero-card-area">
              {cards.isLoading ? <div className="portal-card-skeleton" aria-label="Loading your panel card" />
                : cards.isError && !isNotFoundError(cards.error) ? <PortalErrorState onRetry={() => { void cards.refetch(); }} />
                : card ? <HeroCard card={card} />
                : <div className="portal-no-card"><ShieldCheck size={32} aria-hidden /><p>No panel card has been issued yet.</p></div>}
              <Link className="portal-card-link" to="/portal/$company/card" params={{ company }}>View panel card <ArrowUpRight size={17} aria-hidden /></Link>
            </div>
          )}
        </div>
      </section>

      <div className="portal-home-panels">
        <section className="portal-glass portal-messages" aria-label="Messages">
          <div className="portal-panel-head">
            <span className="portal-panel-icon"><MessageSquare size={23} aria-hidden /></span>
            <Link className="portal-text-link" to="/portal/$company/messages" params={{ company }}>All messages <ArrowUpRight size={17} aria-hidden /></Link>
          </div>
          {messages.isLoading ? <div className="portal-panel-loading" aria-busy="true" />
            : messages.isError && !isNotFoundError(messages.error) ? <PortalErrorState onRetry={() => { void messages.refetch(); }} />
            : inbox.length === 0 ? <div className="portal-message-empty"><p>No messages yet</p><span>Updates about your claims and enquiries will appear here.</span></div>
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
          isLoading={utilization.isLoading}
          error={utilization.isError ? utilization.error : null}
          onRetry={() => { void utilization.refetch(); }}
        />
      </div>

      {care.length > 0 && (
        <section className="portal-glass portal-care" aria-label="Explore your cover">
          <div className="portal-section-head"><h2>Explore your cover</h2><Link className="portal-text-link" to="/portal/$company/coverage" params={{ company }} search={{ tab: "benefits" }}>View all benefits <ArrowRight size={17} aria-hidden /></Link></div>
          <div className="portal-care-grid">
            {care.map((route) => {
              const Icon = route.key === "gp" ? Stethoscope : route.key === "specialist" ? UserRound : Building2;
              return <Link key={route.key} className="portal-care-item" to="/portal/$company/coverage" params={{ company }} search={{ tab: "benefits" }}>{route.key === "dental" ? <ToothIcon /> : <Icon size={29} strokeWidth={1.45} aria-hidden />}<span><strong>{route.title}</strong><small>{route.description}</small></span></Link>;
            })}
          </div>
        </section>
      )}

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
    </div>
  );
}
