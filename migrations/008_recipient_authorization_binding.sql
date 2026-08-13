ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS authorized_recipient_email VARCHAR(320);

-- Existing live authorizations were reviewed against the prospect's then-current
-- verified address. Bind that address so recipient drift is detectable.
UPDATE campaigns c
SET authorized_recipient_email = p.verified_email
FROM prospects p
WHERE c.prospect_id = p.id
  AND c.live_authorized = TRUE
  AND p.email_verified = TRUE
  AND p.verified_email IS NOT NULL
  AND c.authorized_recipient_email IS NULL;

CREATE TABLE IF NOT EXISTS email_verification_audits (
    id SERIAL PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    workspace_id VARCHAR(100) NOT NULL,
    old_email VARCHAR(320),
    new_email VARCHAR(320) NOT NULL,
    verifier_identity VARCHAR(200) NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    invalidated_campaign_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_email_verification_audits_prospect_id
    ON email_verification_audits (prospect_id);
CREATE INDEX IF NOT EXISTS ix_email_verification_audits_workspace_id
    ON email_verification_audits (workspace_id);
