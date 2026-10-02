-- Persisted QA run summaries (inputs + plan + findings + timeline snapshot).
-- JSON payloads live in Postgres; media bytes stay in Neon Object Storage.
--
--   GRANT SELECT, INSERT, UPDATE, DELETE ON runs TO insight_app;

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL UNIQUE,
    bug_description TEXT NOT NULL,
    url TEXT NOT NULL,
    expected_behavior TEXT,
    plan JSONB,
    step_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    timeline JSONB,
    report JSONB,
    error TEXT,
    completed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs (created_at);
CREATE INDEX IF NOT EXISTS runs_url_idx ON runs (url);

REVOKE ALL ON TABLE runs FROM PUBLIC;

-- GRANT SELECT, INSERT, UPDATE, DELETE ON runs TO insight_app;
