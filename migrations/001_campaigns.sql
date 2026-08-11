CREATE TABLE campaigns (
  id BIGSERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL, industry VARCHAR(100) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE prospects (
  id BIGSERIAL PRIMARY KEY, campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  company_name VARCHAR(200) NOT NULL, email VARCHAR(320) NOT NULL, email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100), replied_at TIMESTAMPTZ, opted_out_at TIMESTAMPTZ,
  hard_bounced_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_campaign_email UNIQUE (campaign_id, email)
);
CREATE INDEX ix_prospects_campaign_id ON prospects(campaign_id);
CREATE TABLE deliveries (
  id BIGSERIAL PRIMARY KEY, prospect_id BIGINT NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  day INTEGER NOT NULL CHECK (day IN (0, 3, 6)), scheduled_for TIMESTAMPTZ NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'scheduled', idempotency_key VARCHAR(64) NOT NULL UNIQUE,
  delivered_at TIMESTAMPTZ, CONSTRAINT uq_prospect_day UNIQUE (prospect_id, day)
);
CREATE INDEX ix_deliveries_prospect_id ON deliveries(prospect_id);
CREATE INDEX ix_deliveries_scheduled_for ON deliveries(scheduled_for);
