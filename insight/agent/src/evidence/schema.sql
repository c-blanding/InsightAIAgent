-- Run artifacts metadata (screenshots and video only).
-- Bytes live in Neon buckets. Console/network go to run_logs, not here.
--
--   GRANT SELECT, INSERT ON run_artifacts TO insight_app;

CREATE TABLE IF NOT EXISTS run_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    step INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('screenshot', 'video')),
    bucket TEXT NOT NULL,
    object_key TEXT NOT NULL,
    content_type TEXT,
    byte_size BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_artifacts_thread_step_idx
    ON run_artifacts (thread_id, step);

REVOKE ALL ON TABLE run_artifacts FROM PUBLIC;

-- GRANT SELECT, INSERT ON run_artifacts TO insight_app;

-- If the table was created with console|network kinds, tighten the check:
-- ALTER TABLE run_artifacts DROP CONSTRAINT IF EXISTS run_artifacts_kind_check;
-- ALTER TABLE run_artifacts ADD CONSTRAINT run_artifacts_kind_check
--   CHECK (kind IN ('screenshot', 'video'));
