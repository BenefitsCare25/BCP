# Outbound email — decision + runbook

**Status: decided, NOT implemented (2026-07-27).** The design below is agreed;
nothing has been built or provisioned. Prod currently cannot send email at all.

---

## 1. Current state

`INSPRO_MAIL_MODE=smtp` in prod, but `INSPRO_SMTP_HOST`, `INSPRO_SMTP_USER` and
`INSPRO_SMTP_FROM` are all **empty** (Bicep defaults them to `''` and the deploy
workflow never passes them). `SmtpMailer.__init__` raises on a blank host/sender,
so every send fails.

**Consequence: member portal sign-in does not work in prod.** The OTP is the only
thing the app emails — `app/services/member_otp.py` is the sole `get_mailer()`
call site. Broker invitations deliberately don't email (the invite provisions the
user row; first Entra sign-in matches by email).

`app/core/mailer.py` today: `log` | `smtp` | `acs` (acs raises — never built).

## 2. Decision

**A free Exchange Online shared mailbox, sent through Microsoft Graph with the
App Service managed identity.**

| | |
|---|---|
| Sender address | `benefits-noreply@inspro.com.sg` |
| Display name | `Inspro Benefits Portal` |
| Replies | auto-forward to `helpdesk@inspro.com.sg` (keep a copy) |
| `Reply-To` header | `helpdesk@inspro.com.sg`, stamped by the app on every OTP |
| Auth | App Service **system-assigned managed identity** → Graph `sendMail` |
| Licence cost | **none** — shared mailboxes consume no licence |
| Per-message cost | **none** |
| Secrets to store | **none** — no password, no client secret, nothing to rotate |
| DNS changes needed | **none** — Exchange Online is already covered by SPF |

### Why the mailbox needs no licence

A Microsoft 365 licence is a seat for a *person who signs in*, not a permit for
an address. A shared mailbox has **sign-in blocked at the identity level**, so it
consumes no licence; it's capped at 50 GB with no OneDrive/Teams/archive so it
can't substitute for a seat.

This maps directly onto the auth choice, and is the reason the design is free:

| Approach | What authenticates | Licence needed? |
|---|---|---|
| ROPC (what AIBOT does) | signs in **as a human**, with their password | yes |
| SMTP AUTH | signs in **as the mailbox**, with a password | yes — shared mailboxes can't client-submit |
| **Graph app-only (chosen)** | the **app** authenticates as itself, then sends *as* the mailbox | **no** — nothing signs in |

Had we chosen SMTP or copied AIBOT's pattern, a paid seat would have been
mandatory. Verified 2026-07-27: the tenant has **zero spare seats** (E3 10/10,
Business Standard 4/4, Business Basic 13 owned / 23 assigned), so a user mailbox
would have been genuinely new spend.

## 3. Alternatives considered and rejected

| Option | Cost | Why rejected |
|---|---|---|
| Reuse `BenefitsCare@inspro.com.sg` (AIBOT's sender) | $0 | It is the **Azure subscription Owner** identity. Inspro would hold the admin password in an app setting; ROPC also requires that account to stay MFA-free. Shared sender = no attribution, and one rotation breaks both apps. |
| Reuse `noreply@inspro.com.sg` | $0 | In use by another broker platform, and carries a paid Business Basic licence (likely *because* the vendor signs in as it). Same shared-credential problems. |
| New **licensed** user mailbox | ~US$4–6/user/mo | No spare seats; buys nothing we need. |
| Microsoft 365 SMTP AUTH | $0 | Basic auth for Exchange Online client submission is being retired; building on it means rewiring soon. Also incompatible with a shared mailbox. |
| **Azure Communication Services Email** | ~US$1/mo at 4,000 emails | Viable fallback (see below), but costs money, needs a client secret that expires, and needs 4 DNS records published on the `inspro.com.sg` zone before it can send as that domain. |

### ACS pricing, for the record

Pulled live from the Azure Retail Prices API on 2026-07-27 for `southeastasia`:

- Basic Sent Email — **US$0.00025 per email**
- Basic Data Transferred — **US$0.00012 per MB**
- No monthly fee, no minimum

1,000 members × 4 sign-ins/month ≈ 4,000 emails ≈ **US$1/month**.

**ACS remains the fallback if the M365 admin steps stall.** It needs nothing from
Microsoft 365 — only Azure rights, which we already have. Phase A (an
Azure-managed `…azurecomm.net` sender) works with zero DNS changes and could be
live in under an hour.

## 4. What blocks it

`BenefitsCare@inspro.com.sg` holds **Owner on the Azure subscription** but **no
Microsoft 365 / Entra directory role** (verified: only membership in a group
"Project B C"). Azure RBAC and M365 admin roles are separate systems — which is
why `admin.cloud.microsoft/exchange` renders the self-service view with no
`Recipients`, `Mail flow`, or message trace.

Global Administrators in the tenant (2026-07-27): **Wilson Ong**
(`wilsonong@inspro.com.sg`), `masteradmin1@`, `masteradmin2@`, plus a Check Point
service principal.

All three steps below need Wilson (or an equivalent). Step 3 in particular
requires Global Admin — Cloud Application Administrator cannot consent to Graph
*application* permissions.

## 5. Admin runbook (hand to a Global Admin)

Reference values, all confirmed against the tenant on 2026-07-27:

| | |
|---|---|
| Managed identity | `inspro-portal` (system-assigned, App Service, `rg-inspro-prod`) |
| App ID (client ID) | `48c6e7ff-e3ce-4614-881c-c23291f712d9` |
| Object ID (principal ID) | `2c6468d1-1965-486c-a7d9-a43381ec8d68` |
| Microsoft Graph SP object ID (this tenant) | `6f54b680-3532-440e-a965-b73017fb1659` |
| Graph `Mail.Send` app role ID | `b633e1c5-b582-4048-a93e-9f11b44c7e96` |
| Tenant ID | `496f1a0a-6a4a-4436-b4b3-fdb75d235254` |

### Step 1 — create the shared mailbox

Exchange admin centre → **Recipients → Shared → + Add a shared mailbox**

- Email: `benefits-noreply@inspro.com.sg`
- Display name: `Inspro Benefits Portal`
- Then open it → **Mail flow settings → Email forwarding** → forward to
  `helpdesk@inspro.com.sg`, tick **keep a copy of forwarded messages**

No licence required.

### Step 2 — restrict the app FIRST

Order matters: creating the access policy before granting the permission means
there is never a window in which the app can send as any mailbox.

```powershell
Connect-ExchangeOnline
New-ApplicationAccessPolicy -AppId 48c6e7ff-e3ce-4614-881c-c23291f712d9 `
  -PolicyScopeGroupId benefits-noreply@inspro.com.sg `
  -AccessRight RestrictAccess `
  -Description "Inspro portal OTP sender - restricted to benefits-noreply only"
```

If `-PolicyScopeGroupId` rejects the address, create a mail-enabled security
group containing only that mailbox and scope the policy to the group.

### Step 3 — grant Graph `Mail.Send` to the managed identity

Managed identities have no "API permissions" blade in the portal, so this is
PowerShell only:

```powershell
Connect-MgGraph -Scopes "AppRoleAssignment.ReadWrite.All","Application.Read.All"
New-MgServicePrincipalAppRoleAssignment `
  -ServicePrincipalId 2c6468d1-1965-486c-a7d9-a43381ec8d68 `
  -PrincipalId        2c6468d1-1965-486c-a7d9-a43381ec8d68 `
  -ResourceId         6f54b680-3532-440e-a965-b73017fb1659 `
  -AppRoleId          b633e1c5-b582-4048-a93e-9f11b44c7e96
```

`Mail.Send` (application) is literally named *"Send mail as any user"* and is
tenant-wide by default. **Step 2 is what confines it to one mailbox** — never
grant this without the access policy in place.

### Verify

```powershell
Test-ApplicationAccessPolicy -Identity benefits-noreply@inspro.com.sg -AppId 48c6e7ff-e3ce-4614-881c-c23291f712d9   # expect Granted
Test-ApplicationAccessPolicy -Identity wilsonong@inspro.com.sg        -AppId 48c6e7ff-e3ce-4614-881c-c23291f712d9   # expect Denied
```

## 6. Code work still to do

Not started. Scope:

1. `app/core/mailer.py` — add a `GraphMailer`: acquire a token for
   `https://graph.microsoft.com/.default` via the managed identity
   (`azure-identity`), POST to
   `/v1.0/users/benefits-noreply@inspro.com.sg/sendMail`, set `replyTo` to
   `helpdesk@inspro.com.sg`. Keep `smtp` as a working fallback.
2. `app/core/settings.py` — add `graph` to `MailMode`; extend the prod
   fail-closed guard (`_resolve_mail_mode`) so `log` still refuses to boot in
   prod and a `graph` mode without a configured sender also refuses.
3. New settings: `INSPRO_MAIL_SENDER` (the mailbox address) and
   `INSPRO_MAIL_REPLY_TO`.
4. `infra/bicep/main.bicep` + `parameters.prod.json` — pass `mailMode=graph` and
   the two addresses. No secret parameter needed (that's the point).
5. Tests in `backend/tests/` covering mode resolution and the send payload
   (mock the Graph call — don't hit the network).
6. Dependency: `azure-identity` is likely already present via the Blob storage
   client (`app/core/storage.py`); confirm before adding.

**Verification once live:** request a portal OTP in prod and confirm the response
carries `mail_sent: true`, then confirm the message arrives and that a reply
reaches `helpdesk@`.

## 7. Domain / DNS findings — NOT required for the chosen path

**The shared-mailbox + Graph design needs no DNS change at all** — Exchange
Online is already covered by the domain's SPF. Everything in this section is
separate, pre-existing breakage, recorded only because it degrades the *existing*
platforms' deliverability. Don't read it as a prerequisite.

Where the records live: as of 2026-07-27 `inspro.com.sg` is answered by the
nameservers `dnssec1/2/3.singnet.com.sg`, and there is **no Azure DNS zone for
the domain** in the subscription — so these are not records we can change
ourselves. Confirm who currently administers that zone before raising any change
request; the nameserver operator isn't necessarily who manages the records.

1. **Two SPF records exist** — `v=spf1 include:spf.protection.outlook.com -all`
   and `v=spf1 include:mailgun.org ~all`. RFC 7208 allows exactly one; receivers
   should return `permerror`. Must be merged into a single record. **Do not drop
   the Mailgun include** until the external vendor confirms they don't use it.
2. **`selector2._domainkey` CNAME is missing** (only `selector1` exists), so M365
   DKIM cannot be fully enabled or rotated for the domain.
3. **DMARC is `v=DMARC1; p=none;` with no `rua=`.** A `dmarc-reports@inspro.com.sg`
   mailbox exists but receives nothing. Adding
   `rua=mailto:dmarc-reports@inspro.com.sg` is the only reliable way to learn
   which systems send as the domain — including whether the external vendor sends
   as `donotreply@`.

Also recorded: **`donotreply@inspro.com.sg` does not exist in the tenant** — not
as a mailbox, group, or alias across all 94 `@inspro.com.sg` addresses. An
external vendor can still be sending *as* it via Mailgun without a mailbox, which
is why it wasn't chosen as our sender.

Licence anomaly for the M365 admin to check: Business Basic shows **13 paid seats
but 23 assigned**, with none in warning or suspended state.

## 8. Related security note (different project)

`C:\Users\huien\AIBOT` sends via Graph **ROPC** using
`AZURE_SERVICE_ACCOUNT_USERNAME=BenefitsCare@inspro.com.sg` with the password
stored as a plain app setting on `app-aibot-api`. That account is the Azure
subscription Owner, and ROPC requires it to remain MFA-free. Migrating AIBOT to
the same managed-identity + Graph pattern would remove both problems.
