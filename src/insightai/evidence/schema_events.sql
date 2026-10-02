-- Ordered action / timeline events for a QA run.
-- Run once on the app database (or rely on SQLAlchemy create_tables).
--
--   GRANT SELECT, INSERT, DELETE ON run_events TO insight_app;

CREATE TABLE IF NOT EXISTS run_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL,
    step INTEGER,
    event_type TEXT NOT NULL,
    tool TEXT,
    title TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    ref_kind TEXT,
    ref_bucket TEXT,
    ref_object_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_events_thread_created_idx
    ON run_events (thread_id, created_at);

CREATE INDEX IF NOT EXISTS run_events_thread_step_idx
    ON run_events (thread_id, step);

CREATE INDEX IF NOT EXISTS run_events_thread_type_idx
    ON run_events (thread_id, event_type);

REVOKE ALL ON TABLE run_events FROM PUBLIC;

-- GRANT SELECT, INSERT, DELETE ON run_events TO insight_app;
