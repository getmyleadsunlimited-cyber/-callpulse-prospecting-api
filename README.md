# CallPulse.org Prospecting Agent — Render Free Starter

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
healthCheck → createProspect → listProspects → approveProspect → saveOutreachDraft → recordReply → markConversion

## Important
This free starter stores SQLite data in /tmp, so prospect records are NOT durable across restarts/redeploys. Use it to prove the connection works. For dependable operations, move the prospect database to a persistent PostgreSQL service such as Supabase.

## Current offers
Standard Start: $297 setup + $125/week + $1/recovered lead.
3-Day Proof Trial: $497 setup + 3-day proof period, then $125/week + $1/recovered lead if continued.

Signup:
https://CallPulse.org/AIAppointmentPlatform
