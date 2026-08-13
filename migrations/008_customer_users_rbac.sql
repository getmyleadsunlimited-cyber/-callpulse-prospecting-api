CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(320) NOT NULL UNIQUE,
    password_hash VARCHAR(500) NOT NULL,
    account_id VARCHAR(100) NOT NULL,
    account_type VARCHAR(20) NOT NULL CHECK (account_type IN ('direct', 'agency', 'client')),
    primary_workspace_id VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_account_id ON users (account_id);

CREATE TABLE IF NOT EXISTS user_workspace_access (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id VARCHAR(100) NOT NULL,
    CONSTRAINT uq_user_workspace_access UNIQUE (user_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS ix_user_workspace_access_user_id ON user_workspace_access (user_id);
CREATE INDEX IF NOT EXISTS ix_user_workspace_access_workspace_id ON user_workspace_access (workspace_id);

CREATE TABLE IF NOT EXISTS user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_token_hash ON user_sessions (token_hash);

CREATE TABLE IF NOT EXISTS user_audits (
    id SERIAL PRIMARY KEY,
    account_id VARCHAR(100) NOT NULL,
    actor_user_id INTEGER,
    target_user_id INTEGER NOT NULL,
    action VARCHAR(50) NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_user_audits_account_id ON user_audits (account_id);
CREATE INDEX IF NOT EXISTS ix_user_audits_target_user_id ON user_audits (target_user_id);
