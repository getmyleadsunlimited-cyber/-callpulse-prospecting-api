CREATE TABLE IF NOT EXISTS login_rate_limits (
    key VARCHAR(65) PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts > 0)
);

CREATE TABLE IF NOT EXISTS pending_invitations (
    id SERIAL PRIMARY KEY,
    account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
    email VARCHAR(320) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    primary_workspace_id VARCHAR(100) NOT NULL REFERENCES workspaces(id),
    workspace_ids_json TEXT NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_by_user_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_pending_invitations_account_id ON pending_invitations(account_id);
CREATE INDEX IF NOT EXISTS ix_pending_invitations_email ON pending_invitations(email);
CREATE INDEX IF NOT EXISTS ix_pending_invitations_token_hash ON pending_invitations(token_hash);
CREATE INDEX IF NOT EXISTS ix_pending_invitations_expires_at ON pending_invitations(expires_at);
