# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Insurance brokers (primary operators).** Broker firms administer group-benefit
  programs for their client companies. Roles: `system_admin` (platform/firm ops),
  `broker_admin` (invites users, creates client companies, configures benefits),
  `broker_viewer` (read-only). Work is desk-based, multi-tenant, expert, and
  data-dense — configuring schedules of benefits, matching rosters, running
  enrollment, and adjudicating claims.
- **Insured employees ("members").** End recipients, on a SEPARATE email-OTP
  portal. They view their benefit statement, submit claims (AI-assisted intake),
  manage dependants, find panel clinics, view digital panel cards, and self-elect
  enrollment. Occasional use, consumer-grade expectations, frequently mobile.
- Both surfaces are equally prioritized for design work; they share an identity
  but not the same information density.

## Product Purpose

Inspro turns an insurer's group-benefits **placement slip** into a live,
configured benefits platform — one that brokers administer and covered employees
use directly. It exists to remove the manual, error-prone gap between a signed
insurance slip (PDF/Excel) and an operational program: parsing the slip, matching
employees to coverage, running enrollment, and processing claims. Success = a
broker goes from raw slip → member-usable benefits program with minimal re-keying,
and members self-serve benefits and claims without contacting the broker.

## Positioning

The differentiating mechanism is **slip-driven, registry-based automation end to
end**: AI/registry parsing of heterogeneous insurer placement slips into a
structured schedule-of-benefits + rate/tier model, automatic (tenant/entity-scoped)
employee-to-coverage matching, and an AI claim-review pipeline — all in ONE
multi-tenant platform that also ships the member portal. A neighboring "benefits
admin" tool typically stops at configuration; Inspro carries the same slip through
to member self-service and AI-adjudicated claims.

## Operating Context

- **Multi-tenant:** broker firm → client companies; per-request active-company
  scoping (`X-Inspro-Client`), physical schema-per-firm on Postgres.
- **Broker workflow:** upload/parse placement slip → classify products →
  configure schedule of benefits + rates/tiers → match roster employees → run
  enrollment windows → adjudicate claims (AI review queue) → track utilization →
  generate insurer / fact-find / placement reports; plus panel-clinic locator and
  digital panel e-cards.
- **Member workflow:** OTP sign-in → benefit statement → submit claim (smart
  multi-document intake, AI review) → dependants (self-add + approval) → clinic
  locator → enrollment self-election.
- **Auth:** brokers via Microsoft Entra (DB-backed identity); members via email
  OTP on a separate token surface. Fail-closed in production.
- **Deploy:** containerized to Azure App Service; Postgres + Redis; Singapore
  data residency (Vertex AI Gemini for claims AI).

## Capabilities and Constraints

- Configuration is editable on every policy year; exactly one "current benefit
  year" is what the member portal reads.
- Tenancy isolation is mandatory and enforced (cross-tenant access → 404). The UI
  must never leak one company's data to another.
- Fundamentally **data-dense** on the broker side: schedule-of-benefits matrices,
  rosters, rate/tier tables, claims queues, utilization buckets.
- Core terminology future work must respect: policy year, placement slip, schedule
  of benefits (SOB), tier/cohort, flex benefits, panel clinics, utilization,
  enrollment window, member, dependant.
- Tech constraints: React 18 + Vite + Tailwind v4 (`@theme` tokens) + TanStack
  Router/Query.

## Brand Commitments

- Name: **Inspro** (confirmed, kept).
- The incumbent identity — red `#c11a2b` + warm-neutral palette, Inter — is treated
  as **evidence / anti-reference, NOT a binding constraint**: the user authorized a
  redesign. Only the product name is firmly preserved.
- Binding regardless of visual world (house rules): semantic color tokens only —
  never hardcoded colors; no directional accent borders on components; file-size
  limits. These survive any redesign.

## Evidence on Hand

- Real product surface: broker app (`frontend/src`, routes under
  `configuration/` · `operations/` · `reports/`) and member portal (`/portal/*`).
- Reference docs: `inspro_build_brief.md`, `docs/DEPLOY_RUNBOOK.md`,
  `docs/SECURITY_REVIEW.md`, `docs/ENTITY_MATCHING.md`, `CLAUDE.md`.
- **Absences future work must NOT fabricate:** no public marketing site,
  testimonials, customer/logo references, pricing, or benchmarks exist.
- Real insurer placement slips and employee rosters exist locally but are **PII and
  gitignored** — never surface real member data in design mocks or screenshots.

## Product Principles

1. **Slip → usable program.** Every feature should shorten the path from a raw
   insurer document to something a broker administers or a member uses.
2. **Tenancy safety is non-negotiable.** Never design a flow that could show one
   company's data to another.
3. **Density with clarity.** Broker screens are inherently dense; the design's job
   is scanability and hierarchy, not decoration.
4. **Two audiences, one system.** Brokers (Operate — expert, dense) and members
   (self-serve — occasional, mobile) share a coherent identity, not a density.
5. **Correctness reads visually.** Financial / claims / coverage data must look
   precise and unambiguous; status (approved / pending / flagged) must be instantly
   legible and never confusable with brand color.

## Accessibility & Inclusion

Target **WCAG 2.2 AA**. Rationale: a regulated Singapore insurance product (PDPA)
used by a broad employee population on the member portal. Known open gap: the
current theme defines only a light mode — dark-mode / contrast handling is
unaddressed and in scope for future work.
