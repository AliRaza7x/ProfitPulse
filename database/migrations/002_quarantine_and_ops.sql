-- Data-quality quarantine and pipeline operations metadata.

CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS ops;

-- One row per (message, rule violated). Nothing is dropped silently: rejected
-- messages are kept verbatim; warnings record rows that were accepted anyway.
CREATE TABLE quarantine.dq_failures (
    failure_id       BIGSERIAL    PRIMARY KEY,
    run_id           UUID,
    event_id         TEXT,                         -- may be missing/invalid, hence TEXT
    rule_code        TEXT         NOT NULL,
    severity         TEXT         NOT NULL CHECK (severity IN ('REJECT', 'WARN')),
    disposition      TEXT         NOT NULL CHECK (disposition IN ('REJECTED', 'ACCEPTED_WITH_WARNING')),
    detail           TEXT,
    raw_payload      TEXT         NOT NULL,        -- exact message value as received
    kafka_topic      TEXT,
    kafka_partition  INTEGER,
    kafka_offset     BIGINT,
    detected_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_dq_failures_rule  ON quarantine.dq_failures (rule_code, disposition);
CREATE INDEX ix_dq_failures_event ON quarantine.dq_failures (event_id);
CREATE INDEX ix_dq_failures_run   ON quarantine.dq_failures (run_id);

-- ------------------------------------------------------------------- ops
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    version     TEXT        PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ops.pipeline_runs (
    run_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    stage           TEXT        NOT NULL,   -- publish | ingest | clean | transform | load | analytics | dq
    dag_id          TEXT,
    airflow_run_id  TEXT,
    status          TEXT        NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    rows_in         BIGINT,
    rows_out        BIGINT,
    rows_rejected   BIGINT,
    details         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);
CREATE INDEX ix_pipeline_runs_stage ON ops.pipeline_runs (stage, started_at DESC);

CREATE TABLE ops.dq_results (
    result_id   BIGSERIAL   PRIMARY KEY,
    run_id      UUID,
    layer       TEXT        NOT NULL,       -- ingest | clean | warehouse | analytics
    check_name  TEXT        NOT NULL,
    severity    TEXT        NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'INFO')),
    status      TEXT        NOT NULL CHECK (status IN ('PASS', 'FAIL', 'WARN')),
    observed    NUMERIC,
    expected    NUMERIC,
    message     TEXT,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_dq_results_run ON ops.dq_results (run_id);

-- Next offset to read per topic partition (written only after a batch lands).
CREATE TABLE ops.kafka_offsets (
    topic        TEXT        NOT NULL,
    partition_id INTEGER     NOT NULL,
    next_offset  BIGINT      NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (topic, partition_id)
);

-- Which offset range each raw-zone batch covers (audit + reconciliation).
CREATE TABLE ops.ingest_batches (
    batch_id      TEXT        NOT NULL,
    topic         TEXT        NOT NULL,
    partition_id  INTEGER     NOT NULL,
    from_offset   BIGINT      NOT NULL,
    until_offset  BIGINT      NOT NULL,   -- exclusive
    messages      BIGINT      NOT NULL,
    run_id        UUID,
    landed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_id, topic, partition_id)
);

GRANT USAGE ON SCHEMA quarantine, ops TO ${READER_ROLE};
GRANT SELECT ON ALL TABLES IN SCHEMA quarantine, ops TO ${READER_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA quarantine GRANT SELECT ON TABLES TO ${READER_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO ${READER_ROLE};
