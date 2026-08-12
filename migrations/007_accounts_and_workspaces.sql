CREATE TABLE accounts (
  id BIGSERIAL PRIMARY KEY, account_type VARCHAR(20) NOT NULL,
  agency_name VARCHAR(200), status VARCHAR(40) NOT NULL DEFAULT 'active', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_account_type CHECK (account_type IN ('direct', 'agency'))
);
CREATE INDEX ix_accounts_account_type ON accounts(account_type);
CREATE INDEX ix_accounts_status ON accounts(status);
CREATE TABLE client_workspaces (
  id BIGSERIAL PRIMARY KEY, account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  client_business_name VARCHAR(200) NOT NULL, industry VARCHAR(100), website VARCHAR(500),
  status VARCHAR(40) NOT NULL DEFAULT 'active', white_label_display_name VARCHAR(200), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_client_workspaces_account_id ON client_workspaces(account_id);
CREATE INDEX ix_client_workspaces_status ON client_workspaces(status);
INSERT INTO accounts (account_type, status) VALUES ('direct', 'active');
INSERT INTO client_workspaces (account_id, client_business_name, status)
SELECT id, 'CallPulse Direct', 'active' FROM accounts WHERE account_type='direct' ORDER BY id LIMIT 1;
ALTER TABLE prospects ADD COLUMN workspace_id BIGINT REFERENCES client_workspaces(id) ON DELETE CASCADE;
ALTER TABLE campaigns ADD COLUMN workspace_id BIGINT REFERENCES client_workspaces(id) ON DELETE CASCADE;
ALTER TABLE campaign_touches ADD COLUMN workspace_id BIGINT REFERENCES client_workspaces(id) ON DELETE CASCADE;
ALTER TABLE suppressions ADD COLUMN id BIGSERIAL;
ALTER TABLE suppressions ADD COLUMN workspace_id BIGINT REFERENCES client_workspaces(id) ON DELETE CASCADE;
ALTER TABLE canary_execution_audits ADD COLUMN workspace_id BIGINT REFERENCES client_workspaces(id) ON DELETE CASCADE;
UPDATE prospects SET workspace_id=(SELECT id FROM client_workspaces LIMIT 1);
UPDATE campaigns SET workspace_id=(SELECT id FROM client_workspaces LIMIT 1);
UPDATE campaign_touches SET workspace_id=(SELECT id FROM client_workspaces LIMIT 1);
UPDATE suppressions SET workspace_id=(SELECT id FROM client_workspaces LIMIT 1);
UPDATE canary_execution_audits SET workspace_id=(SELECT id FROM client_workspaces LIMIT 1);
ALTER TABLE prospects ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE campaigns ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE campaign_touches ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE suppressions ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE canary_execution_audits ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE suppressions DROP CONSTRAINT suppressions_pkey;
ALTER TABLE suppressions ADD PRIMARY KEY (id);
ALTER TABLE prospects DROP CONSTRAINT IF EXISTS prospects_verified_email_key;
ALTER TABLE prospects ADD CONSTRAINT uq_workspace_prospect_email UNIQUE(workspace_id, verified_email);
ALTER TABLE suppressions ADD CONSTRAINT uq_workspace_suppression_email UNIQUE(workspace_id, email);
CREATE INDEX ix_prospects_workspace_id ON prospects(workspace_id);
CREATE INDEX ix_campaigns_workspace_id ON campaigns(workspace_id);
CREATE INDEX ix_campaign_touches_workspace_id ON campaign_touches(workspace_id);
CREATE INDEX ix_suppressions_workspace_id ON suppressions(workspace_id);
CREATE INDEX ix_canary_execution_audits_workspace_id ON canary_execution_audits(workspace_id);
