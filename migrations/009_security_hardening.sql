CREATE TABLE IF NOT EXISTS accounts (
    id VARCHAR(100) PRIMARY KEY,
    account_type VARCHAR(20) NOT NULL CHECK (account_type IN ('direct', 'agency', 'client'))
);
CREATE TABLE IF NOT EXISTS workspaces (
    id VARCHAR(100) PRIMARY KEY,
    owner_account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
    workspace_type VARCHAR(20) NOT NULL CHECK (workspace_type IN ('direct', 'agency', 'client'))
);
CREATE INDEX IF NOT EXISTS ix_workspaces_owner_account_id ON workspaces(owner_account_id);
CREATE TABLE IF NOT EXISTS agency_workspace_access (
    id SERIAL PRIMARY KEY,
    agency_account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
    workspace_id VARCHAR(100) NOT NULL REFERENCES workspaces(id),
    CONSTRAINT uq_agency_workspace UNIQUE (agency_account_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS ix_agency_workspace_access_agency_account_id ON agency_workspace_access(agency_account_id);
CREATE INDEX IF NOT EXISTS ix_agency_workspace_access_workspace_id ON agency_workspace_access(workspace_id);

INSERT INTO accounts(id, account_type)
SELECT DISTINCT account_id, account_type FROM users ON CONFLICT (id) DO NOTHING;

-- Deployments with ambiguous legacy IDs must pre-populate this registry explicitly.
-- Unique legacy ownership can be migrated without guessing.
CREATE TABLE IF NOT EXISTS workspace_ownership_registry (
    workspace_id VARCHAR(100) PRIMARY KEY,
    owner_account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
    workspace_type VARCHAR(20) NOT NULL CHECK (workspace_type IN ('direct', 'agency', 'client'))
);
INSERT INTO workspace_ownership_registry(workspace_id, owner_account_id, workspace_type)
SELECT primary_workspace_id, MIN(account_id), MIN(account_type)
FROM users WHERE primary_workspace_id IS NOT NULL
GROUP BY primary_workspace_id
HAVING COUNT(DISTINCT account_id) = 1
ON CONFLICT (workspace_id) DO NOTHING;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM users u WHERE u.primary_workspace_id IS NOT NULL
        GROUP BY u.primary_workspace_id HAVING COUNT(DISTINCT u.account_id) > 1
        AND NOT EXISTS (SELECT 1 FROM workspace_ownership_registry r
                        WHERE r.workspace_id = u.primary_workspace_id)
    ) THEN
        RAISE EXCEPTION 'Ambiguous legacy workspace ownership: populate workspace_ownership_registry explicitly before migration';
    END IF;
    IF EXISTS (
        SELECT 1 FROM workspace_ownership_registry r
        JOIN accounts a ON a.id = r.owner_account_id
        WHERE r.workspace_type <> a.account_type OR
              NOT EXISTS (SELECT 1 FROM users u WHERE u.primary_workspace_id = r.workspace_id
                          AND u.account_id = r.owner_account_id)
    ) THEN
        RAISE EXCEPTION 'Workspace ownership registry owner is not a legacy account for that workspace';
    END IF;
END $$;
INSERT INTO workspaces(id, owner_account_id, workspace_type)
SELECT workspace_id, owner_account_id, workspace_type FROM workspace_ownership_registry
ON CONFLICT (id) DO NOTHING;

ALTER TABLE users ADD COLUMN IF NOT EXISTS security_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ALTER COLUMN account_id DROP NOT NULL;
ALTER TABLE users ALTER COLUMN account_type DROP NOT NULL;
ALTER TABLE users ALTER COLUMN primary_workspace_id DROP NOT NULL;
ALTER TABLE users ALTER COLUMN role DROP NOT NULL;
ALTER TABLE users ALTER COLUMN active DROP NOT NULL;

CREATE TABLE IF NOT EXISTS account_memberships (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
    primary_workspace_id VARCHAR(100) NOT NULL REFERENCES workspaces(id),
    role VARCHAR(20) NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_user_account UNIQUE(user_id, account_id)
);
CREATE INDEX IF NOT EXISTS ix_account_memberships_user_id ON account_memberships(user_id);
CREATE INDEX IF NOT EXISTS ix_account_memberships_account_id ON account_memberships(account_id);
INSERT INTO account_memberships(user_id, account_id, primary_workspace_id, role, active)
SELECT id, account_id, primary_workspace_id, role, active FROM users
WHERE account_id IS NOT NULL ON CONFLICT (user_id, account_id) DO NOTHING;
UPDATE account_memberships m SET role = 'owner'
WHERE m.id = (SELECT MIN(candidate.id) FROM account_memberships candidate
              WHERE candidate.account_id = m.account_id AND candidate.active = TRUE)
  AND NOT EXISTS (SELECT 1 FROM account_memberships own
                  WHERE own.account_id = m.account_id AND own.active = TRUE AND own.role = 'owner');

CREATE TABLE IF NOT EXISTS membership_workspace_access (
    id SERIAL PRIMARY KEY,
    membership_id INTEGER NOT NULL REFERENCES account_memberships(id) ON DELETE CASCADE,
    workspace_id VARCHAR(100) NOT NULL REFERENCES workspaces(id),
    CONSTRAINT uq_membership_workspace UNIQUE(membership_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS ix_membership_workspace_access_membership_id ON membership_workspace_access(membership_id);
CREATE INDEX IF NOT EXISTS ix_membership_workspace_access_workspace_id ON membership_workspace_access(workspace_id);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM users u
        LEFT JOIN workspace_ownership_registry r ON r.workspace_id = u.primary_workspace_id
        WHERE u.account_id IS NOT NULL AND
              (r.workspace_id IS NULL OR r.owner_account_id <> u.account_id)
    ) THEN
        RAISE EXCEPTION 'Invalid migrated primary workspace ownership';
    END IF;
    IF EXISTS (
        SELECT 1 FROM user_workspace_access a
        JOIN users u ON u.id = a.user_id
        LEFT JOIN workspace_ownership_registry r ON r.workspace_id = a.workspace_id
        LEFT JOIN accounts owner_account ON owner_account.id = r.owner_account_id
        WHERE r.workspace_id IS NULL OR NOT (
            r.owner_account_id = u.account_id OR
            (u.account_type = 'agency' AND owner_account.account_type = 'client')
        )
    ) THEN
        RAISE EXCEPTION 'Invalid legacy workspace grant; add authoritative ownership/delegation or quarantine the grant';
    END IF;
END $$;
INSERT INTO agency_workspace_access(agency_account_id, workspace_id)
SELECT DISTINCT u.account_id, a.workspace_id FROM user_workspace_access a
JOIN users u ON u.id = a.user_id AND u.account_type = 'agency'
JOIN workspaces w ON w.id = a.workspace_id AND w.owner_account_id <> u.account_id
ON CONFLICT (agency_account_id, workspace_id) DO NOTHING;
INSERT INTO membership_workspace_access(membership_id, workspace_id)
SELECT m.id, a.workspace_id FROM user_workspace_access a
JOIN account_memberships m ON m.user_id = a.user_id
JOIN workspaces w ON w.id = a.workspace_id
ON CONFLICT (membership_id, workspace_id) DO NOTHING;

-- Pre-hardening sessions had no expiry or membership binding and are intentionally invalidated.
DELETE FROM user_sessions;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS membership_id INTEGER REFERENCES account_memberships(id) ON DELETE CASCADE;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS security_version INTEGER;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
ALTER TABLE user_sessions ALTER COLUMN membership_id SET NOT NULL;
ALTER TABLE user_sessions ALTER COLUMN security_version SET NOT NULL;
ALTER TABLE user_sessions ALTER COLUMN expires_at SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_user_sessions_membership_id ON user_sessions(membership_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_expires_at ON user_sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_security_events (
    id SERIAL PRIMARY KEY,
    account_key VARCHAR(64) NOT NULL,
    source_key VARCHAR(64) NOT NULL,
    succeeded BOOLEAN NOT NULL DEFAULT FALSE,
    reason VARCHAR(40) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_login_security_events_account_key ON login_security_events(account_key);
CREATE INDEX IF NOT EXISTS ix_login_security_events_source_key ON login_security_events(source_key);
CREATE INDEX IF NOT EXISTS ix_login_security_events_created_at ON login_security_events(created_at);

CREATE TABLE IF NOT EXISTS workspace_audits (
    id SERIAL PRIMARY KEY,
    workspace_id VARCHAR(100) NOT NULL,
    account_id VARCHAR(100) NOT NULL,
    action VARCHAR(50) NOT NULL,
    actor_user_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_workspace_audits_workspace_id ON workspace_audits(workspace_id);
CREATE INDEX IF NOT EXISTS ix_workspace_audits_account_id ON workspace_audits(account_id);
