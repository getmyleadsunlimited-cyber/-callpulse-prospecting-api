-- PostgreSQL reference migration. SQLAlchemy metadata creates the same schema for new installs.
CREATE TABLE IF NOT EXISTS campaigns (
 id BIGSERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL, industry VARCHAR(80) NOT NULL,
 geography VARCHAR(200) NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
 daily_first_touch_limit INTEGER NOT NULL DEFAULT 25, sending_window JSONB NOT NULL,
 timezone VARCHAR(80) NOT NULL, minimum_score INTEGER NOT NULL DEFAULT 65,
 allowed_priority_levels JSONB NOT NULL, auto_approve_qualified_prospects BOOLEAN NOT NULL DEFAULT TRUE,
 verified_business_email_required BOOLEAN NOT NULL DEFAULT TRUE, duplicate_suppression BOOLEAN NOT NULL DEFAULT TRUE,
 opt_out_suppression BOOLEAN NOT NULL DEFAULT TRUE, hard_bounce_suppression BOOLEAN NOT NULL DEFAULT TRUE,
 stop_on_reply BOOLEAN NOT NULL DEFAULT TRUE, automatic_prospect_replenishment BOOLEAN NOT NULL DEFAULT TRUE,
 status VARCHAR(20) NOT NULL DEFAULT 'draft', created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS priority VARCHAR(2) DEFAULT 'B';
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS verified_facts JSONB DEFAULT '[]';
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS normalized_email VARCHAR(320);
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES campaigns(id);
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS campaign_approved BOOLEAN DEFAULT FALSE;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS suppression_status VARCHAR(30) DEFAULT 'clear';
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS sequence_step INTEGER DEFAULT 0;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS next_send_at TIMESTAMPTZ;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS sent_count INTEGER DEFAULT 0;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS reply_detected BOOLEAN DEFAULT FALSE;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS bounced BOOLEAN DEFAULT FALSE;
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS opted_out BOOLEAN DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_prospect_email ON prospects(campaign_id, normalized_email);
CREATE TABLE IF NOT EXISTS deliveries (
 id BIGSERIAL PRIMARY KEY, prospect_id BIGINT NOT NULL REFERENCES prospects(id), campaign_id BIGINT NOT NULL REFERENCES campaigns(id),
 sequence_step INTEGER NOT NULL, idempotency_key VARCHAR(200) NOT NULL UNIQUE, provider_message_id VARCHAR(300) NOT NULL,
 delivered_at TIMESTAMPTZ NOT NULL, UNIQUE(prospect_id, sequence_step)
);
