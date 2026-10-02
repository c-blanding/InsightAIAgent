-- Run once on the auth database, as a role that can create tables.
-- The application role only needs SELECT, INSERT, UPDATE, and DELETE.
--
--   GRANT SELECT, INSERT, UPDATE, DELETE ON auth_sessions TO insight_auth;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id text NOT NULL UNIQUE,
    origin text NOT NULL,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    key_version integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx
    ON auth_sessions (expires_at);

REVOKE ALL ON auth_sessions FROM PUBLIC;

-- Then, as a role that can grant, allow only the application role:
-- GRANT SELECT, INSERT, UPDATE, DELETE ON auth_sessions TO insight_auth;
