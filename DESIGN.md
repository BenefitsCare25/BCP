---
name: Inspro
description: Visual authority for the broker, employee and HR portals.
---

# Inspro design sources

The employee and HR portals follow the user-approved Home and enrolment implementation. Their requirements are recorded in [the scoped design memory](docs/EMPLOYEE_HR_PORTAL_DESIGN_MEMORY.md), including later refinements to Coverage, My family and claim submission feedback.

## Employee and HR portals

Use Figtree typography, compact white or coordinated benefit-pastel cards, navy supporting text and blue general controls. Preserve benefit colours through hover states, visible blue keyboard focus and translucent blue/lilac gradient scrollbars. Remove repetitive headings and descriptions, align related content, and keep mobile layouts compact without clipping or undersized controls.

Employee Home and HR Overview share the animated landscape. HR Overview greets the signed-in HR user and places its module cards below the scene. Other portal pages retain static backgrounds. Show the full registered company name. Both portals share the rounded header treatment, tab icons and account controls.

My family lists dependants only. Employee claim forms and lists are centred within capped containers. Claim submission confirmation is a short inline status inside the claim summary; automatic submission history is concise. Preserve factual terms, calculations, access rules and working actions.

Implementation sources:

- [Shared Home card styles](frontend/src/styles/portal-clay.css)
- [Scoped portal theme](frontend/src/styles/portal-ui.css)
- [Home components and landscape](frontend/src/components/portal/home)
- [Employee shell](frontend/src/components/portal/PortalShell.tsx)
- [HR shell](frontend/src/components/hr/HrShell.tsx)

## Employee and HR sign-in (local exception)

Only the employee and HR sign-in surfaces use reference-red controls (#df0024), red keyboard focus and Figtree with a full-height, unframed white desktop panel. The Inspro logo sits directly above the heading within the form block. No plane or trails remain. The opaque scenery video moves, with a matching still for reduced motion, data saving or playback failure. Skin tones receive a selective red-cast correction and gentle brightness lift, as requested, without regenerating the people or changing motion. Preserve company, credential, MFA, reset and tenant-routing flows. Scope this exception to `.portal-login`; password setup, authenticated portals and broker/admin themes retain their existing guidance.

The latest user direction replaces the feathered blend with a clean split and only a softly rounded upper-left corner on the white panel: 64px desktop, 32px stacked. Extend the photo behind the complete corner. No gradient, blur, SVG divider or other rounded panel corners. See the [sign-in surface brief](.impeccable/surfaces/frontend-src-routes-portal-sign-in-tsx.md) for layout, media and evidence. Fresh build and all 41 browser groups pass after this change, including absent plane/trails, no-fade geometry and face clearance. This remains a local preview without production approval.

## Broker portal

Retain the broker shell's existing Inter typography, warm neutral tokens, red primary actions and semantic statuses. Broker screens are operational interfaces with readable tables, clear context and compact controls. Employee/HR styling must not alter broker surfaces through unscoped shared CSS.

Use [broker navigation](docs/BROKER_NAVIGATION.md) for page names and [broker interface guidance](docs/BROKER_UI.md) for member/coverage conventions. The [AI Settings surface](frontend/src/features/ai-settings/DESIGN.md) follows the broker system.

### Broker sign-in exception

The broker `/sign-in` surface uses Figtree, red primary controls and keyboard focus, a Marina Bay photograph with optional motion, and an unframed white form panel. On wide screens the panel begins at 62% with an organic white curve over the scenery; tablet and phone layouts stack the image above the form. Keep the Inspro logo, heading and real Microsoft Entra action together. The reference's email/password fields are not part of broker authentication.

Scope this exception to `.broker-login`. Preserve the woman with glasses, the man without glasses, prominent orchids and visible water in the scenery. Provide a pause/play control, still-image fallbacks for reduced motion, data saving or playback failure, and pause playback in hidden tabs. Employee/HR sign-in and authenticated portal guidance remain unchanged. See the [broker sign-in surface brief](.impeccable/surfaces/frontend-src-routes-auth-sign-in-tsx.md) for implementation details and the [release record](docs/BROKER_LOGIN_RELEASE_2026-10-04.md) for verification and deployment status.

The previous portal visual specification is retained as [historical design rationale](docs/archive/PORTAL_DESIGN_2026-09-25.md). It is superseded within employee and HR portals.
