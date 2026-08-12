-- Persist explicit live authorization and delivery-level safety/audit state.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS dry_run BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS live_authorized BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS live_authorized_at TIMESTAMPTZ;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS live_authorized_by VARCHAR(200);

ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS dry_run BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS skipped BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS cancelled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS cancellation_or_skip_reason VARCHAR(300);

-- The baseline already creates this constraint. This also protects upgraded databases.
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_touches_idempotency_key
  ON campaign_touches(idempotency_key);
