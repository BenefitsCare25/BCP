# Microsoft Entra ID Setup

Step-by-step Azure Portal walkthrough to register the Inspro app and switch
the backend from mock auth to Entra JWT validation.

Audience: Azure global admin with permissions to create app registrations in
the tenant.

## 1. App registration

1. **Azure Portal** → Microsoft Entra ID → **App registrations** → **+ New registration**.
2. **Name**: `Inspro Platform — Staging` (repeat for Prod).
3. **Supported account types**: *Accounts in this organizational directory only* (single-tenant).
   - If you serve multiple broker firms across Entra tenants → choose multi-tenant + add a sign-in policy.
4. **Redirect URI** → Single-page application (SPA):
   - Staging: `https://inspro-staging.azurewebsites.net/auth/callback`
   - Prod: `https://app.inspro.example/auth/callback`
   - Local dev: `http://localhost:5173/auth/callback`
5. Click **Register**.

After registration, copy these values into the App Service config (or Key Vault):

| Portal field | Env var |
|---|---|
| Directory (tenant) ID | `INSPRO_ENTRA_TENANT_ID` |
| Application (client) ID | `INSPRO_ENTRA_CLIENT_ID` |

## 2. Expose an API

1. App registration → **Expose an API** → **Add** an Application ID URI: `api://inspro`.
2. **Add a scope**: name `access_as_user`, description "Access Inspro as the signed-in user", admin consent required.
3. Copy `api://inspro` to env var `INSPRO_ENTRA_AUDIENCE` (or leave unset to default to the client ID).

## 3. Token configuration

App registration → **Token configuration** → **Add groups claim**:

- *Groups assigned to the user* → Group ID (default).
- Include in **Access** and **ID** tokens.

This populates the `groups` claim that `app/core/entra.py::role_from_claims`
maps to Inspro roles.

## 4. Role mapping

Create an Entra security group for system administrators (Inspro internal
staff who need cross-tenant visibility), copy its object ID, and set:

```
INSPRO_ENTRA_GROUP_ROLE_MAP=<group_object_id>:system_admin
```

Add more `<group_id>:<role>` pairs separated by commas for `broker_admin`,
`broker_viewer`, `client_admin`, `client_hr` as needed.

## 4a. Who may sign in at all (two independent gates)

Access is granted by the platform's OWN user list, not by Entra. A colleague in
the same Microsoft tenant who is not in `users` authenticates fine with
Microsoft and is then refused by the API with a coded
`403 {"code": "no_access"}` — the SPA sends them to `/no-access` and never
renders the app shell. So the app is safe by default.

Close the outer gate too, so an unprovisioned person can't even reach the
Microsoft consent screen for this app:

Entra admin center → **Enterprise applications** → *Inspro* → **Properties** →
set **Assignment required?** to **Yes**, then **Users and groups** → **Add
user/group** and assign only the intended users (or a security group).

With assignment required, an unassigned user gets Microsoft's own
"You can't access this application" page at sign-in. Without it they get as far
as the `/no-access` page. Provision people in both places: assign the Entra
group AND invite them under `/admin` (the DB row is what grants role + firm).

## 5. API permissions (for the SPA)

The SPA needs `openid`, `profile`, `email`, and your `access_as_user` scope.

App registration → **API permissions** → **Add a permission**:
- Microsoft Graph → Delegated → `openid`, `profile`, `email`, `User.Read`
- My APIs → Inspro → `access_as_user`

Click **Grant admin consent**.

## 6. App Service config

Set these on the App Service (or pull from Key Vault):

```
INSPRO_AUTH_MODE=entra
INSPRO_ENTRA_TENANT_ID=<tenant_id>
INSPRO_ENTRA_CLIENT_ID=<client_id>
INSPRO_ENTRA_AUDIENCE=api://inspro
INSPRO_ENTRA_GROUP_ROLE_MAP=<group_id>:<role>,...
```

Restart the App Service. New requests must present a Bearer JWT or receive 401.

## 7. Frontend

Set in `frontend/.env.production`:

```
VITE_ENTRA_TENANT_ID=<tenant_id>
VITE_ENTRA_CLIENT_ID=<client_id>
VITE_ENTRA_AUDIENCE=api://inspro
VITE_API_BASE_URL=https://<your-app-service>/api/v1
```

The frontend uses `@azure/msal-react` for PKCE login (see
`frontend/src/auth/msal.ts`).

## 8. Verification

1. Visit the staging frontend URL.
2. Click **Sign in** → redirected to `login.microsoftonline.com` → consent.
3. After redirect back to `/auth/callback`, the SPA exchanges the auth code
   for an access token and stores it in memory.
4. Call any protected endpoint via the SPA (e.g. `GET /policy-years`).
5. Backend logs show `verify_entra_token` succeeded with the `oid` claim.

## 9. Rollback

If something breaks, flip `INSPRO_AUTH_MODE=mock` on the App Service and
restart. All routes revert to the demo-client identity. Use this only for
emergency mitigation — mock-auth in prod has no access control.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 401 "audience mismatch" | `INSPRO_ENTRA_AUDIENCE` doesn't match the token's `aud` claim. Check Expose-an-API URI. |
| 401 "issuer mismatch" | Token was issued by a different tenant. Confirm `INSPRO_ENTRA_TENANT_ID`. |
| 401 "no matching JWK" | JWKS cache stale; restart App Service to refetch, or wait 24h. |
| 401 "token expired" | Client should refresh via MSAL silently — check the SPA console. |
| All requests fall back to demo role | `INSPRO_ENTRA_GROUP_ROLE_MAP` not set or group membership not provisioned. |
| Signs in with Microsoft, lands on "You don't have access yet" | Working as designed — no `users` row for that oid/email. Invite them under `/admin`; see §4a. |
