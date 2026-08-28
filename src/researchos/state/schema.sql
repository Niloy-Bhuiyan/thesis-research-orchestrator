-- ResearchOS persistent state. SQLite is the authoritative store:
-- no experiment state may live only in an LLM conversation.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    root_path           TEXT NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'manual',
    policy_path         TEXT,
    methodology_version TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (mode IN ('manual', 'auto_exploration', 'locked_evaluation'))
);

-- One scientific attempt. Lineage via parent_id; rejected experiments are
-- kept, never deleted, so selective reporting stays hard.
CREATE TABLE IF NOT EXISTS experiments (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id),
    parent_id           TEXT REFERENCES experiments(id),
    hypothesis          TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed',
    mode                TEXT NOT NULL,
    git_sha             TEXT,
    branch              TEXT,
    methodology_version TEXT,
    config_hash         TEXT,
    notebook_hash       TEXT,
    dataset             TEXT,
    dataset_version     TEXT,
    seeds               TEXT,
    primary_metric_name TEXT,
    primary_metric      REAL,
    metric_direction    TEXT,
    failure_class       TEXT,
    provider            TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    ended_at            TEXT,
    CHECK (status IN ('proposed','approved','rejected','preparing','queued',
                      'running','completed','failed','cancelled','paused','imported'))
);

-- One execution attempt of an experiment: a Kaggle kernel run, or an external
-- manual run imported later. An experiment gets several runs via retries.
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL REFERENCES experiments(id),
    attempt           INTEGER NOT NULL DEFAULT 1,
    backend           TEXT NOT NULL DEFAULT 'kaggle',
    kernel_ref        TEXT,
    kernel_version    INTEGER,
    accelerator       TEXT,
    internet_enabled  INTEGER,
    status            TEXT NOT NULL DEFAULT 'pending',
    exit_reason       TEXT,
    log_path          TEXT,
    output_path       TEXT,
    manifest_json     TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    created_at        TEXT NOT NULL,
    CHECK (backend IN ('kaggle','external_manual','local')),
    CHECK (status IN ('pending','submitted','running','complete','error','cancelled','timeout'))
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    run_id        TEXT REFERENCES runs(id),
    name          TEXT NOT NULL,
    value         REAL NOT NULL,
    split         TEXT,
    seed          INTEGER,
    step          INTEGER,
    recorded_at   TEXT NOT NULL
);

-- Agent-proposed change awaiting policy validation and/or human approval.
CREATE TABLE IF NOT EXISTS proposals (
    id                TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL REFERENCES experiments(id),
    kind              TEXT NOT NULL,
    summary           TEXT NOT NULL,
    detail            TEXT,
    diff              TEXT,
    target_field      TEXT,
    scientific_impact TEXT,
    policy_verdict    TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_by        TEXT,
    created_at        TEXT NOT NULL,
    decided_at        TEXT,
    decided_by        TEXT,
    CHECK (status IN ('pending','approved','rejected','auto_applied','expired'))
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id                TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL REFERENCES experiments(id),
    run_id            TEXT REFERENCES runs(id),
    failure_class     TEXT NOT NULL,
    subclass          TEXT,
    confidence        REAL NOT NULL,
    evidence          TEXT,
    proposed_action   TEXT,
    scientific_impact TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 1,
    provider          TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    run_id        TEXT REFERENCES runs(id),
    kind          TEXT NOT NULL,
    path          TEXT NOT NULL,
    sha256        TEXT,
    bytes         INTEGER,
    created_at    TEXT NOT NULL
);

-- Append-only structured event stream. Drives dashboard and Telegram.
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT,
    experiment_id TEXT,
    run_id        TEXT,
    level         TEXT NOT NULL DEFAULT 'info',
    kind          TEXT NOT NULL,
    message       TEXT NOT NULL,
    data          TEXT,
    created_at    TEXT NOT NULL,
    CHECK (level IN ('debug','info','warn','error','critical'))
);

-- Provider invocation ledger. Records what actually happened; usage numbers
-- the provider does not expose are left NULL rather than invented.
CREATE TABLE IF NOT EXISTS provider_calls (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(id),
    provider      TEXT NOT NULL,
    model         TEXT,
    task          TEXT,
    outcome       TEXT NOT NULL,
    error_class   TEXT,
    duration_ms   INTEGER,
    started_at    TEXT NOT NULL,
    ended_at      TEXT
);

-- Commands arriving from the dashboard via the coordination layer.
CREATE TABLE IF NOT EXISTS commands (
    id            TEXT PRIMARY KEY,
    project_id    TEXT,
    type          TEXT NOT NULL,
    params        TEXT,
    issued_by     TEXT NOT NULL,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    result        TEXT,
    created_at    TEXT NOT NULL,
    acked_at      TEXT,
    completed_at  TEXT,
    CHECK (status IN ('pending','acked','running','done','failed','rejected','expired'))
);

-- Daemon singleton row plus budget counters, so limits survive restarts.
CREATE TABLE IF NOT EXISTS daemon_state (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    status                   TEXT NOT NULL DEFAULT 'stopped',
    pid                      INTEGER,
    last_heartbeat           TEXT,
    active_experiment_id     TEXT,
    pause_reason             TEXT,
    session_started_at       TEXT,
    experiments_this_session INTEGER NOT NULL DEFAULT 0,
    consecutive_failures     INTEGER NOT NULL DEFAULT 0,
    provider_calls_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_exp_project ON experiments(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_exp_parent  ON experiments(parent_id);
CREATE INDEX IF NOT EXISTS idx_exp_status  ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_runs_exp    ON runs(experiment_id, attempt);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_metrics_exp ON metrics(experiment_id, name);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_exp  ON events(experiment_id, id);
CREATE INDEX IF NOT EXISTS idx_prop_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_cmd_status  ON commands(status, created_at);
