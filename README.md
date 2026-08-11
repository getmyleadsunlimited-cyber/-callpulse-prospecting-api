# CallPulse.org Prospecting Campaign API

Production deployments require a PostgreSQL `DATABASE_URL`. Apply SQL files in
`migrations/` in numeric order before starting the service. The local SQLite
fallback is intended only for development.

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

The checked-in `openapi.yaml` documents the campaign, prospect, suppression,
industry, launcher, and delivery-run endpoints. Requests require the bearer API
key except for `/`, `/health`, `/launcher`, and the generated OpenAPI document.

Prospects are deduplicated by campaign and normalized email. Every prospect is
scheduled for Day 0, Day 3, and Day 6. A delivery is eligible only when its
score is at least 65 and its email is verified; replies, opt-outs, and hard
bounces cancel every remaining scheduled delivery.

## Safety
`POST /deliveries/run` advances due delivery records without connecting to an
email provider. This makes the API safe to validate without production
credentials or sending real email. A separately reviewed delivery worker is
required to perform actual delivery.

## Current offers
Standard Start: $297 setup + $125/week + $1/recovered lead.
3-Day Proof Trial: $497 setup + 3-day proof period, then $125/week + $1/recovered lead if continued.

Signup:
https://CallPulse.org/AIAppointmentPlatform
