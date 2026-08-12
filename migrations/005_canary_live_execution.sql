-- Persist fail-closed, single-recipient canary claims, results, and non-secret audit data.
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS execution_status VARCHAR(20) NOT NULL DEFAULT 'pending';
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS execution_started_at TIMESTAMPTZ;
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS execution_completed_at TIMESTAMPTZ;
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS provider_name VARCHAR(40);
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(300);
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS last_execution_error VARCHAR(500);
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS execution_attempt_count INTEGER NOT NULL DEFAULT 0;

-- Conservatively preserve historical success; unsent rows remain pending and are not newly authorized.
UPDATE campaign_touches
SET execution_status = 'sent', execution_completed_at = COALESCE(execution_completed_at, sent_at)
WHERE sent_at IS NOT NULL AND execution_status = 'pending';

CREATE INDEX IF NOT EXISTS ix_campaign_touches_execution_status ON campaign_touches(execution_status);

CREATE TABLE IF NOT EXISTS canary_execution_audits (
  id SERIAL PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  delivery_id INTEGER NOT NULL REFERENCES campaign_touches(id) ON DELETE CASCADE,
  prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
  authorized_by VARCHAR(200) NOT NULL,
  authorization_timestamp TIMESTAMPTZ,
  execution_requested_at TIMESTAMPTZ NOT NULL,
  sender_identity VARCHAR(320) NOT NULL,
  recipient_email VARCHAR(320) NOT NULL,
  idempotency_key VARCHAR(64) NOT NULL,
  provider_name VARCHAR(40) NOT NULL,
  provider_message_id VARCHAR(300),
  result VARCHAR(30) NOT NULL,
  failure_reason VARCHAR(1000)
);
CREATE INDEX IF NOT EXISTS ix_canary_execution_audits_campaign_id ON canary_execution_audits(campaign_id);
CREATE INDEX IF NOT EXISTS ix_canary_execution_audits_delivery_id ON canary_execution_audits(delivery_id);
CREATE INDEX IF NOT EXISTS ix_canary_execution_audits_prospect_id ON canary_execution_audits(prospect_id);
CREATE INDEX IF NOT EXISTS ix_canary_execution_audits_idempotency_key ON canary_execution_audits(idempotency_key);
