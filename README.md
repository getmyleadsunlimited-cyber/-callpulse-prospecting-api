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

Authentication:
API Key / Bearer

Use the value Render generated for:
CALLPULSE_ACTIONS_API_KEY

Paste the updated openapi.yaml as the schema.

Test:
healthCheck → listIndustryButtons → createProspect → launchSevenDayCampaign → runDueCampaignTouches → recordReplyAndStopCampaign → recordConversion

## Production campaign

The service supports the business verticals exposed at `/launcher` and qualifies only prospects scoring at least 65 with independently verified business emails. Selecting a vertical supplies its CallPulse AI Website Lead Recovery opening angle. Launching a campaign creates exactly three idempotent touches for Day 0, Day 3, and Day 6 and closes the seven-day campaign on Day 7. Replies, conversions, opt-outs, hard-bounce suppressions, and other suppressions immediately stop further outreach.

Set `DATABASE_URL` to PostgreSQL. Render runs `python migrate.py` before every API start, applying each pending SQL migration transactionally and checking every SQLAlchemy model table and column before serving traffic. Set a strong `CALLPULSE_ACTIONS_API_KEY`; authentication fails closed when it is missing. The launcher is safe by default (`CALLPULSE_DRY_RUN=true`). After configuring and validating an HTTPS `CALLPULSE_DELIVERY_WEBHOOK`, explicitly set dry-run to `false` and schedule `python launcher.py` from a trusted cron with `CALLPULSE_API_URL` and the API key. The adapter receives `to`, `message`, and `idempotency_key`; failed deliveries remain scheduled for retry.

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
