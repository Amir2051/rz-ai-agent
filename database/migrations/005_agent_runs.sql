-- Agent run ledger for bounded MIA tool-loop execution.
CREATE TABLE IF NOT EXISTS rz_agent_runs (
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed','interrupted')),
    user_request TEXT NOT NULL,
    events JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_response TEXT,
    error TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, run_id)
);
CREATE INDEX IF NOT EXISTS rz_agent_runs_user_updated_idx
    ON rz_agent_runs (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS rz_agent_runs_user_status_idx
    ON rz_agent_runs (user_id, status);
