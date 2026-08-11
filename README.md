# CallPulse Autonomous Prospecting API

A PostgreSQL-backed FastAPI service for autonomous, compliance-aware seven-day prospecting campaigns. Campaigns qualify verified business contacts, suppress duplicates and opted-out/bounced contacts, and run a Day 0 / Day 3 / Day 6 sequence with at most three messages.

## Local setup

```bash
export DATABASE_URL=postgresql+psycopg://user:password@localhost/callpulse
export CALLPULSE_ACTIONS_API_KEY=replace-me
pip install -r requirements.txt
uvicorn app:app --reload
```

For an existing database, apply `migrations/001_autonomous_campaigns.sql`. New databases are initialized on application startup. Never use the SQLite test configuration in production.

## Houston Roofing launch

POST `/campaigns` with the following production-ready initial configuration, then POST `/campaigns/{id}/start`:

```json
{"name":"Houston Roofing — 7 Day","industry":"Roofing","geography":"Houston, TX","start_date":"2026-08-11","daily_first_touch_limit":25,"timezone":"America/Chicago","minimum_score":65,"allowed_priority_levels":["A","B"],"verified_business_email_required":true,"stop_on_reply":true,"opt_out_suppression":true,"hard_bounce_suppression":true,"automatic_prospect_replenishment":true}
```

The launcher is at `/launcher`; campaign state and stats are exposed through authenticated API actions.

## Microsoft Graph

Set `MICROSOFT_GRAPH_ACCESS_TOKEN` and `MICROSOFT_GRAPH_SENDER` at runtime. `outlook.send_mail` calls Graph and raises on missing credentials or rejected requests. It never fabricates delivery. After Graph accepts a request, record it using `recordDelivery`; use its stable `campaign:{campaign}:prospect:{prospect}:step:{step}` key so retries are idempotent. Inbound reply, opt-out, and hard-bounce webhooks should immediately call their event action before another worker claims due work.

## Tests

```bash
pytest -q
```
