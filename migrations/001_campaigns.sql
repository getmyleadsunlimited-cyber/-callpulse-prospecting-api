-- PostgreSQL baseline migration for autonomous seven-day campaigns.
CREATE TABLE IF NOT EXISTS prospects (
  id BIGSERIAL PRIMARY KEY, company_name VARCHAR(200) NOT NULL, website VARCHAR(500) NOT NULL,
  industry VARCHAR(100) NOT NULL CHECK (industry IN ('Final Expense','Auto Insurance')),
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100), why_now TEXT NOT NULL,
  ai_recovery_opportunity TEXT NOT NULL, decision_maker_name VARCHAR(200), decision_maker_title VARCHAR(200),
  verified_email VARCHAR(320) UNIQUE NOT NULL, email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  opening_message TEXT, status VARCHAR(40) NOT NULL DEFAULT 'researched', last_reply TEXT,
  intent VARCHAR(50), conversion_stage VARCHAR(50), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ix_prospects_status ON prospects(status);
CREATE TABLE IF NOT EXISTS campaigns (id BIGSERIAL PRIMARY KEY, prospect_id BIGINT UNIQUE NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  status VARCHAR(30) NOT NULL DEFAULT 'active', starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_touches (id BIGSERIAL PRIMARY KEY, campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  day INTEGER NOT NULL CHECK(day IN (0,3,6)), scheduled_at TIMESTAMPTZ NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'scheduled',
  message TEXT NOT NULL, idempotency_key VARCHAR(64) UNIQUE NOT NULL, sent_at TIMESTAMPTZ, UNIQUE(campaign_id, day));
CREATE INDEX IF NOT EXISTS ix_due_touches ON campaign_touches(status, scheduled_at);
CREATE TABLE IF NOT EXISTS suppressions (email VARCHAR(320) PRIMARY KEY, reason VARCHAR(200) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
