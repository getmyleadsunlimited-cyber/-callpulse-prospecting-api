-- Preserve all existing rows as CallPulse Direct while making new data tenant-scoped.
ALTER TABLE prospects ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(100) NOT NULL DEFAULT 'callpulse-direct';
CREATE INDEX IF NOT EXISTS ix_prospects_workspace_id ON prospects (workspace_id);
ALTER TABLE prospects DROP CONSTRAINT IF EXISTS prospects_verified_email_key;
ALTER TABLE prospects ADD CONSTRAINT uq_prospect_workspace_email UNIQUE (workspace_id, verified_email);

ALTER TABLE suppressions ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(100) NOT NULL DEFAULT 'callpulse-direct';
ALTER TABLE suppressions DROP CONSTRAINT IF EXISTS suppressions_pkey;
ALTER TABLE suppressions ADD PRIMARY KEY (email, workspace_id);
