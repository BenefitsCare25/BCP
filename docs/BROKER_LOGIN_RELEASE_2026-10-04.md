# Broker sign-in release — 2026-10-04

Status: **deployed and verified in production**. Release `b7eb5af34a37f6b9c400531c44a18871f2d1ec2c` completed deployment on 2026-10-04 at 11:50:02 SGT (03:50:02 UTC). The user authorized deployment without another visual review.

## Change

The broker `/sign-in` page now follows the supplied screenshot composition: Marina Bay scenery on the left, an organic white panel curve, Figtree typography, red controls and a stacked layout on tablets and phones. The source scene retains the woman with glasses, the man without glasses and prominent orchids. Scenery motion includes water, foliage and hair.

Microsoft Entra remains the actual authentication path; the reference's email/password fields were not fabricated. Disabled configuration, denied access/account switching, pending submission, duplicate-click protection, error feedback and retry remain covered. Motion has an accessible pause/play control, still fallbacks for reduced motion/data saving/playback failure, and hidden-tab pausing. Styling is confined to `.broker-login`.

Implementation:

- [Sign-in route](../frontend/src/routes/auth/sign-in.tsx)
- [Scene layout](../frontend/src/components/auth/BrokerLoginScene.tsx), [motion behavior](../frontend/src/components/auth/BrokerLoginScenery.tsx) and [scoped styles](../frontend/src/components/auth/broker-login.css)
- [Focused browser tests](../frontend/e2e/broker-login.spec.ts)
- [Surface brief](../.impeccable/surfaces/frontend-src-routes-auth-sign-in-tsx.md)

No employee/HR or authenticated-interface styling changes are part of this broker sign-in work. No database changes, secrets or personal records are included. Unrelated workflow, Docker, authentication-audience and handover edits are outside this release's scope.

## Media provenance

The user supplied `Sunny Marina Bay waterfront gathering.png`, SHA256 `CAFBDB84FFBEBDED679033272AB0D032F1D79EDE493B9AAC525D82171F95E185`.

Higgsfield Seedance 2.5 job `bd34f1b6-f57d-4596-ab71-6510eeb3a41c` requested a square, silent, 8-second 1080p result at high bitrate. The provider returned 1440×1440 HEVC with 193 frames. The job used 96 credits (433 before, 337 after). The exact [request record](../output/broker-login/seedance-1080p-motion-v1.request.json) and [prompt](../output/broker-login/seedance-1080p-motion-v1.prompt.txt) remain local generation evidence.

Browser assets:

| Asset | Format | Bytes | SHA256 |
| --- | --- | ---: | --- |
| [Video](../frontend/public/broker/login/marina-bay-v1.mp4) | H.264/yuv420p, 1080×1080, 24fps, 181 frames, 7.541667s, silent | 1,860,577 | `269D09A142D7481B3D76F59F5F0A9629C4701ECC8137C16F1992E3BDEC73B6E6` |
| [First-frame poster](../frontend/public/broker/login/marina-bay-v1.webp) | WebP, 1080×1080 | 213,260 | `1594A0A2EACF5884A3E723E6196E762779D260F9EA134F0DC8DB63B184327DD3` |

The poster has an adjacent [provenance sidecar](../frontend/public/broker/login/marina-bay-v1.webp.json). A 12-frame/0.5-second tail-to-head blend prepares repeat playback; there is no reversed playback or facial mask. The Downloads delivery `Inspro-Broker-Login-Seedance-1080p-Preview.mp4` repeats the loop three times; `Inspro-Broker-Login-Seedance-1080p-Original.mp4` retains the provider source.

Motion is measurable in water, orchids, foliage and hair. The man's hair/clothing movement is subtler, and small generated eye changes may occur. No claim of perfect facial immobility or a perfectly seamless loop is made.

## Local verification

The implementation run completed these checks before documentation:

| Check | Result |
| --- | --- |
| `pnpm build` | Passed |
| `pnpm audit --prod` | No known vulnerabilities |
| Focused Playwright broker sign-in suite | 14 desktop/mobile tests passed, covering layout/accessibility, motion, fallbacks, account picker, pending submission, errors, retry and duplicate clicks |
| Local QA | 14 groups passed; 12 viewport layouts from 1920px to 320px, three actual browser video wraps and denied-access rendering |
| Browser accessibility | Desktop and phone axe scans: zero violations |
| Scoped Impeccable detector | No findings (`[]`) |
| Raster provenance scan | One raster, zero missing provenance |
| Independent finish review | **SHIP**, no material findings |

The [local QA report](../tmp/broker-login-release-20261004/local/checks.json) and neighboring captures contain the layout/motion evidence, with no recorded failures or page errors. Reviewed [desktop](../.impeccable/review/broker-login/desktop.png) and [mobile](../.impeccable/review/broker-login/mobile.png) screenshots are retained locally. Generated evidence paths may be gitignored and are not production artifacts.

Limits: no physical-device Safari check or real-user-credential completion was performed. Production verification reached the real Microsoft authorization endpoint without submitting credentials.

## Deployment evidence

The release workflow and independent live checks completed successfully. The deployed portal and review worker both reported the exact release SHA through `X-Inspro-Version`; `/readiness` separately returned healthy database and Redis status.

| Field | Recorded result |
| --- | --- |
| Commit SHA | `b7eb5af34a37f6b9c400531c44a18871f2d1ec2c` |
| Push/branch | Pushed to `BenefitsCare25/BCP`, `main` |
| CI run URL and conclusion | [Deploy run 37174814049](https://github.com/BenefitsCare25/BCP/actions/runs/37174814049): **success**. Production frontend build and dependency audit passed; full browser suite: **165 passed, 1 skipped**. Backend/static jobs were skipped by the workflow's UI-only change classification. |
| Deployment run/revision and completion time | Production deployment succeeded at 2026-10-04 11:50:02 SGT; exact release SHA verified on portal and worker; workflow stable readiness window passed. |
| Live origin and sign-in route | [Broker sign-in](https://inspro-portal.azurewebsites.net/sign-in) |
| Live page/media responses and served asset hashes | Live page rendered successfully. Both media GETs returned 200 and matched the hashes above. MP4 byte-range request returned 206 with the requested 1,024 bytes. Portal health, readiness and worker readiness returned 200; database and Redis reported `ok`. |
| Live desktop/mobile, motion/fallback and authentication checks | **14 live QA groups passed**, including 12 widths from 1920px to 320px, desktop/mobile axe scans with zero violations, reduced-motion stills without MP4 requests, three actual 1080×1080 video wraps, measurable water/orchid/foliage/hair movement, pause/resume and denied-access rendering. **9 production verification checks passed**, including visible keyboard focus, enabled Microsoft action and real Entra redirect with the correct production callback, scope and code flow. HR and employee pages retained their existing scene and role controls. No page errors recorded. |
| Final release disposition | **SHIPPED**. Independent finish review and production verification passed. |

Local evidence: [live QA report](../tmp/broker-login-release-20261004/live/checks.json), [production verification](../tmp/broker-login-release-20261004/live/production-verification.json), and reviewed [desktop](../.impeccable/review/broker-login/desktop-live.png) / [mobile](../.impeccable/review/broker-login/mobile-live.png) captures. A documentation-only follow-up records these observations; the deployed application revision remains the release SHA above.
