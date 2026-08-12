# CallPulse.org Autonomous Prospecting API

Use this package to test your GPT Actions on Render's free web-service tier.

## Deploy
1. Create a private GitHub repo.
2. Upload all files from this package.
3. In Render choose New → Blueprint.
4. Connect the repo.
5. Render reads render.yaml and creates the free FastAPI web service.
6. When live, open your .onrender.com URL and then /health.
7. Replace the placeholder server URL in openapi.yaml with your actual Render URL.
8. Commit that change.

## GPT Action
Open CallPulse.org Prospecting Agent → Configure → Actions → Create new action.

Authentication uses a bearer credential whose tenant grants are configured by the service (never by the request).

Paste the updated openapi.yaml as the schema.

Test:
healthCheck → listIndustryButtons → createProspect → launchSevenDayCampaign → inspectProspectCampaigns → inspectCampaignDeliveries → runDueCampaignTouches → recordReplyAndStopCampaign → recordConversion

### Authentication and workspace authorization

`X-Workspace-ID` is only a selector among grants already bound to the authenticated bearer credential; it is never authorization by itself. Configure customer credentials with `CALLPULSE_TENANT_CREDENTIALS`, a JSON object keyed by secret bearer token:

```json
{
  "direct-secret": {"role": "direct", "workspace_id": "direct-42"},
  "agency-secret": {"role": "agency", "workspace_id": "agency-7", "client_workspace_ids": ["client-8", "client-9"]},
  "client-secret": {"role": "client", "workspace_id": "client-8"}
}
```

A direct or client credential can use only its own workspace. An agency credential can use its agency workspace and only the client workspaces explicitly listed in its grant. Omitting `X-Workspace-ID` selects the credential's own workspace; supplying a foreign workspace returns `403`. Once a workspace is authorized, IDs are looked up only in that workspace, so missing or foreign resource IDs return `404` to limit tenant enumeration. This boundary applies uniformly to prospects, campaigns, deliveries, suppressions, replies, conversions, canary execution/audits, and the runner.

The legacy `CALLPULSE_ACTIONS_API_KEY` is retained solely as a direct credential for `callpulse-direct`; it cannot switch tenants. `CALLPULSE_INTERNAL_ADMIN_API_KEY` is an optional, separate unrestricted internal-admin credential and must never be issued to an agency, client, or direct customer.

### Customer users and roles

Customer users are persistent members of an account and authenticate with `POST /auth/login`. Passwords must be at least 12 characters and are stored only as salted PBKDF2-SHA256 hashes; login returns a random opaque bearer token whose SHA-256 digest, rather than the token itself, is persisted. Deactivating a user revokes all of that user's sessions. `GET /me` returns the authenticated identity, account binding, role, and authorized workspace set.

Roles are enforced server-side on every state-changing operation:

* **owner** — full workspace control and the only customer role that may list, create/invite, change the role of, deactivate, or change workspace access for users;
* **admin** — prospect/campaign operations, suppressions, replies/conversions, safety authorization, canary execution, and workspace operations, but no user management or ownership transfer;
* **member** — read operational data and create prospects/campaigns, but no users, tenant credentials, suppression/reply/conversion administration, or live execution controls;
* **viewer** — read-only operational access.

An internal administrator bootstraps an account owner through `POST /users` by supplying `account_id`, `account_type`, and `primary_workspace_id`; subsequent owner-created users inherit those account fields. User administration is available at `GET/POST /users`, `PATCH /users/{user_id}/role`, `POST /users/{user_id}/deactivate`, and `PUT /users/{user_id}/workspace-access`. Creation, role change, deactivation, and workspace-grant replacement write immutable `user_audits` records.

Every customer session resolves its workspace authorization from persisted user grants. Direct-account and client users are restricted to their primary workspace. Agency users may select their agency workspace or explicitly granted client workspaces only. Owners cannot grant a workspace they cannot themselves access, and `X-Workspace-ID` remains only a selector. Foreign workspace selection is `403`; resource lookup remains scoped first and returns `404` for foreign IDs. The legacy bearer grants and the separate internal-admin credential remain supported and are not weakened.

## Production campaign

The service supports the business verticals exposed at `/launcher` and qualifies only prospects scoring at least 65 with independently verified business emails. Selecting a vertical supplies its CallPulse AI Website Lead Recovery opening angle. Launching a campaign creates exactly three idempotent touches for Day 0, Day 3, and Day 6 and closes the seven-day campaign on Day 7. Replies, conversions, opt-outs, hard-bounce suppressions, and other suppressions immediately stop further outreach.

## Read-only campaign inspection

Both inspection routes require an authorized bearer credential. `GET /prospects/{prospect_id}/campaigns` returns campaign identity, prospect and industry, status, start/end timestamps, current Day 0/3/6 sequence state, stopped state, and the current process dry-run setting. `GET /campaigns/{campaign_id}/deliveries` returns the persisted touches in sequence order with IDs, scheduling, full message, delivery status, sent timestamp, derived skipped/cancelled flags, and the opaque idempotency key. They perform reads only: neither route launches the runner, sends a message, nor commits a database transaction. A missing parent resource returns `404`.

The schema persists campaign and delivery dry-run state, explicit live authorization audit fields, stable delivery idempotency keys, and delivery cancellation/skip reasons. Inspection responses never include API keys, delivery-provider credentials, database URLs, or access tokens.

## Live Execution Safety Gate

Every new campaign and delivery starts in dry-run mode and is not live authorized:

**Dry Run → Safety Checks → Explicit Authorization → Eligible for Future Execution**

An authenticated operator must call `POST /campaigns/{campaign_id}/authorize-live` with a non-blank `authorized_by` and the exact confirmation `AUTHORIZE LIVE OUTREACH`. The API checks active campaign state, prospect suppression, a verified valid destination, deliveries, and stable idempotency keys before atomically recording who authorized the campaign and when. It then transitions only future, unsent, non-skipped, non-cancelled deliveries out of dry-run mode. Repeating the same authorization is idempotent and does not recreate deliveries, change timing, or replace idempotency keys.

`GET /campaigns/{campaign_id}/safety` provides a read-only safety and eligibility summary. A later suppression remains an absolute stop: future deliveries are skipped with a persisted suppression reason while sent history is retained. **Authorization only establishes eligibility for a future executor; it does not send email, SMS, calls, or social messages.** This API does not invoke a messaging provider as part of authorization or safety inspection.

Set `DATABASE_URL` to PostgreSQL. Render runs `python migrate.py` before every API start, applying each pending SQL migration transactionally and checking every SQLAlchemy model table and column before serving traffic. Configure strong, unique tenant credentials; authentication fails closed when no configured credential matches. New campaigns are safe by default regardless of process configuration. No messaging-provider setup is part of this safety-gate release.

## Canary Live Execution

The deliberately manual lifecycle is:

**Dry Run → Safety Inspection → Explicit Live Authorization → Canary Preflight → Explicit ONE-Delivery Canary → Persist Result → Manual Review**

Use authenticated `GET /deliveries/{delivery_id}/canary-preflight` to inspect current delivery, campaign, recipient, suppression, stop-state, sender, and idempotency gates without changing state. Then call `POST /campaigns/{campaign_id}/canary-execute` with the persisted delivery ID, a non-empty operator identity, and the exact confirmation `EXECUTE ONE CANARY DELIVERY`. The endpoint accepts neither message content nor batch size; it uses the exact persisted email message.

**ONE request = maximum ONE email transmission attempt.** An atomic database claim transitions only the named delivery to `sending`. Concurrent requests cannot own it, and a retry after confirmed success reads the persisted result without sending again. Results are persisted on the delivery and in a non-secret audit record. `GET /deliveries/{delivery_id}/execution` is read-only. No automatic retry, follow-up, Day 3/Day 6 execution, scheduler, worker, or campaign-wide send is enabled.

Real delivery remains fail-closed by default:

```text
CALLPULSE_EMAIL_PROVIDER=disabled
CALLPULSE_EMAIL_FROM=
```

The deterministic `mock` provider is test-only and performs no network I/O.

## Microsoft Graph Email Provider

Email delivery defaults to disabled and must be explicitly configured through environment variables only:

```text
CALLPULSE_EMAIL_PROVIDER=microsoft_graph
CALLPULSE_EMAIL_FROM=approved-sender@example.com
MICROSOFT_TENANT_ID=<Microsoft Entra tenant ID>
MICROSOFT_CLIENT_ID=<application client ID>
MICROSOFT_CLIENT_SECRET=<application client secret>
```

`CALLPULSE_EMAIL_FROM` is the sole approved sender; callers cannot override it. The provider obtains an in-memory, short-lived OAuth 2.0 client-credentials token from the Microsoft identity platform with the `https://graph.microsoft.com/.default` scope, then calls `POST /v1.0/users/{approved-sender}/sendMail` with the exact persisted delivery subject and message. Tokens and credentials are never persisted or returned. Graph `sendMail` normally returns `202 Accepted` without a message resource, so the API records a safe Graph request correlation ID rather than falsely claiming a message ID.

The Entra application requires administrator-consented Microsoft Graph **Mail.Send application permission**, and the approved sender must be a valid mailbox the application is permitted to use. Tenant administrators should restrict application mailbox access according to their Microsoft 365 policy. Authentication, permission, sender, throttling, service, timeout, and network failures fail closed and are not automatically retried.

Authenticated `GET /email-provider/status` reports only provider mode, readiness, approved sender, and `live_send_enabled: false`; it performs no network request. Canary preflight includes the same non-secret readiness information. Canary execution still requires explicit live authorization and all existing safety gates. One canary request can attempt at most one external email. Automatic campaign sending, background workers, bulk sending, and automatic retries are not enabled.

## Development

```sh
python -m pip install -r requirements-dev.txt
pytest -q
```

## Current offers
Standard Start: $297 setup + $125/week + $1/recovered lead.
3-Day Proof Trial: $497 setup + 3-day proof period, then $125/week + $1/recovered lead if continued.

Signup:
https://CallPulse.org/AIAppointmentPlatform
