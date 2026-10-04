# Broker sign-in release — 2026-10-04

Status: local implementation and finish review complete; push, CI, deployment and live verification pending. The user authorized deployment without another visual review. This record does not establish a successful deployment.

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

Limits: no physical-device Safari check or real-user-credential completion was performed. Local verification and the finish review do not replace CI or live verification.

## Deployment evidence — pending

Append observed results here after deployment; do not infer them from local checks.

| Field | Recorded result |
| --- | --- |
| Commit SHA | Pending |
| Push/branch | Pending |
| CI run URL and conclusion | Pending |
| Deployment run/revision and completion time | Pending |
| Live origin and sign-in route | Pending |
| Live page/media responses and served asset hashes | Pending |
| Live desktop/mobile, motion/fallback and authentication checks | Pending |
| Final release disposition | Pending |
