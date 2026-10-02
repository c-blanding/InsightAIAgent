-- Console / network / snapshot / info logs for a QA run (text in Postgres, not object storage).
-- Run once on the app database. The application role only needs SELECT, INSERT, DELETE.
--
--   GRANT SELECT, INSERT, DELETE ON run_logs TO insight_app;

CREATE TABLE IF NOT EXISTS run_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    step INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('console', 'network', 'info', 'snapshot')),
    source TEXT,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_logs_thread_step_idx
    ON run_logs (thread_id, step);

CREATE INDEX IF NOT EXISTS run_logs_thread_kind_idx
    ON run_logs (thread_id, kind);

REVOKE ALL ON TABLE run_logs FROM PUBLIC;

-- Then, as a role that can grant, allow only the application role:
-- GRANT SELECT, INSERT, DELETE ON run_logs TO insight_app;

-- If the table was created before snapshot kind existed:
-- ALTER TABLE run_logs DROP CONSTRAINT IF EXISTS run_logs_kind_check;
-- ALTER TABLE run_logs ADD CONSTRAINT run_logs_kind_check
--   CHECK (kind IN ('console', 'network', 'info', 'snapshot'));
