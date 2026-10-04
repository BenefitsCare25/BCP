---
version: 1
slug: "frontend-src-routes-auth-sign-in-tsx"
primary_target: "frontend/src/routes/auth/sign-in.tsx"
related_targets: ["frontend/src/components/auth/BrokerLoginScene.tsx", "frontend/src/components/auth/BrokerLoginScenery.tsx", "frontend/src/components/auth/broker-login.css", "frontend/e2e/broker-login.spec.ts"]
---

# Broker sign-in

Scope: the broker `/sign-in` entry point. The user authorized the supplied screenshot direction, Seedance scenery and deployment without another visual review. This surface is deployed and verified in production; exact revision, CI and live evidence are recorded in the [release record](../../docs/BROKER_LOGIN_RELEASE_2026-10-04.md).

Authority: [product context](../../PRODUCT.md), the [broker sign-in exception](../../DESIGN.md#broker-sign-in-exception), and the source files above. All visual rules are scoped to `.broker-login`; employee/HR sign-in and authenticated interfaces keep their existing guidance.

## Appearance and layout

- Figtree Variable/Figtree; white surface, dark ink and muted supporting text. Scoped semantic tokens in `broker-login.css` define red primary/focus (#cf2030), hover (#ae1525), disabled fill (#f5cbd1) and disabled/alert text (#762331).
- Desktop scenery fills the left 62% and extends behind an organic white SVG panel curve. The curve is 64px wide, reduced to 48px at 901–1200px. The white panel is full-height and unframed.
- Keep the logo above “Welcome back,” the broker subtitle and Microsoft action in one left-aligned form block capped at 380px. The primary action is at least 54px high with 7px corners, a visible pending state and a 3px red keyboard outline offset by 4px.
- At widths up to 900px, or aspect ratios up to 6:5, stack the scenery above the form and remove the desktop curve. The banner is `clamp(250px, 48vw, 420px)` high; at widths up to 360px it is 225px. Keep both faces visible, reserve footer space and allow content to grow vertically.
- Preserve the source scene's woman with glasses, man without glasses, prominent orchids and Marina Bay water. The scenery is decorative and hidden from assistive technology. The separate 44px pause/play button remains keyboard accessible.

## Authentication and motion

Broker authentication remains Microsoft Entra. The supplied reference informs composition, not credential capabilities: do not add email/password, registration or password-reset controls to this route. Preserve the configuration-disabled state, the denied-access alert and Microsoft account picker, pending/disabled submission, duplicate-click protection, error announcement and retry.

Use `/broker/login/marina-bay-v1.mp4` with `/broker/login/marina-bay-v1.webp` as the matching first-frame still. The browser video is silent H.264/yuv420p, 1080×1080, 24fps, 181 frames and 7.541667 seconds. It plays inline and loops. It becomes visible only after playback starts; reduced motion or `saveData` prevents mounting/downloading the MP4, while media errors or rejected autoplay retain the still. Hidden tabs pause playback and the explicit pause choice survives visibility changes. Reduced motion also disables CSS transitions.

The Seedance 2.5 source received a 12-frame/0.5-second tail-to-head blend for repeat playback, without reversed playback or facial masks. Water, orchids, foliage and hair visibly change. The man's hair/clothing motion is subtler; small generated eye changes remain possible. Do not describe the faces as perfectly immobile or the loop as perfectly seamless. Exact source, provenance, hashes and generation details are in the release record and adjacent poster sidecar.

## Review evidence and limits

The implementation run passed the production build, production dependency audit, 14 focused desktop/mobile Playwright tests and 14 local QA groups. The [local report](../../tmp/broker-login-release-20261004/local/checks.json) records 12 viewport widths (1920, 1440, 1280, 1024, 940, 901, 900, 768, 697, 600, 390 and 320px), no overflow/page errors, motion across three browser loop wraps and denied-access rendering. Desktop and phone axe checks reported zero violations.

The scoped Impeccable detector returned no findings; provenance scanning found one raster and no missing provenance. The independent finish review returned **SHIP**, with no material findings, using [desktop](../review/broker-login/desktop.png) and [mobile](../review/broker-login/mobile.png) captures. CI passed 165 browser tests with one skipped. Production passed all 14 layout/motion QA groups and nine release checks, including the exact portal/worker revision, matching media hashes, healthy database/Redis, real Microsoft authorization handoff and preserved HR/employee pages. Reviewed live [desktop](../review/broker-login/desktop-live.png) and [mobile](../review/broker-login/mobile-live.png) captures are retained locally. These checks do not establish physical-device Safari behavior or completion with real user credentials.
