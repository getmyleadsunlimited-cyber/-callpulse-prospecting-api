-- Persist the exact subject and Graph correlation metadata without storing tokens or credentials.
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS subject VARCHAR(300) NOT NULL DEFAULT 'A practical lead recovery idea';
ALTER TABLE campaign_touches ADD COLUMN IF NOT EXISTS provider_correlation_id VARCHAR(300);
ALTER TABLE canary_execution_audits ADD COLUMN IF NOT EXISTS provider_correlation_id VARCHAR(300);
