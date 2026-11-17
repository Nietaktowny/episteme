-- The event log, and the dimensions around it.
--
-- `events` is the bronze layer: append-only, loose, a few fixed columns and a JSON payload. Nothing here knows what
-- the payloads hold; views promote the fields that matter to columns (silver), and answer questions (gold).
-- The dimensions (dim_*) describe what the log says, for joins in BI tools. dim_severity and dim_date are filled
-- here; dim_event_type and dim_rule are kept equal to the catalogs in the code (models/events.py) every time a
-- database opens.
--
--   * occurred_at is when it happened (a frontmatter date, a file time, or the time it was noticed), inserted_at is
--     when the row was written: they differ, and the second is for debugging the first. Both are UTC.
--   * occurred_date is occurred_at's day, UTC, computed by the database.
--   * Events are never updated. They are soft-deleted: set deleted_at and is_deleted follows; nothing can set one
--     without the other because is_deleted is computed.
--   * dedupe_key is unique, so recording an event twice stores it once.
--   * entity_id is a note path, a task id and so on; NULL for an event about a run (a sync).
--   * event_version is the version of the payload's shape, and for health events the version of the rule.

CREATE TABLE events (
    event_id      TEXT PRIMARY KEY,
    system        TEXT NOT NULL,
    category      TEXT NOT NULL CHECK (category IN ('state', 'health', 'system')),
    event_type    TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    rule_id       TEXT,
    entity_id     TEXT,
    occurred_at   TIMESTAMP NOT NULL,
    inserted_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    occurred_date DATE GENERATED ALWAYS AS (CAST(occurred_at AS DATE)),
    run_id        TEXT,
    producer      TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL UNIQUE,
    deleted_at    TIMESTAMP,
    is_deleted    BOOLEAN GENERATED ALWAYS AS (deleted_at IS NOT NULL),
    payload       JSON NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_events_type_time            ON events (event_type, occurred_at);
CREATE INDEX idx_events_entity_time          ON events (entity_id, occurred_at);
CREATE INDEX idx_events_system_category_time ON events (system, category, occurred_at);
CREATE INDEX idx_events_occurred_date        ON events (occurred_date);
CREATE INDEX idx_events_rule                 ON events (rule_id, occurred_at);
CREATE INDEX idx_events_run                  ON events (run_id);


CREATE TABLE dim_severity (
    severity      TEXT PRIMARY KEY,
    severity_rank INTEGER NOT NULL,
    description   TEXT NOT NULL
);
INSERT INTO dim_severity VALUES
    ('info',  1, 'Worth knowing; nothing is wrong yet or the cost of ignoring it is small.'),
    ('warn',  2, 'Something drifts from the conventions; fix it when convenient.'),
    ('error', 3, 'Something is broken (an invalid value, a dangling link, a failed sync); fix it soon.');


-- One row per kind of event; the code's catalog, current version only.
CREATE TABLE dim_event_type (
    event_type      TEXT PRIMARY KEY,
    system          TEXT NOT NULL,
    category        TEXT NOT NULL,
    current_version INTEGER NOT NULL,
    description     TEXT NOT NULL
);


-- One row per rule *version*: when a rule's logic or its effective parameters change, the row is closed
-- (valid_to) and a new one starts, so an old violation still reads against the rule as it was.
-- rule_sk is the single-column key that BI tools need; events join on (rule_id, event_version = rule_version).
CREATE TABLE dim_rule (
    rule_sk       INTEGER PRIMARY KEY,
    rule_id       TEXT NOT NULL,
    rule_version  INTEGER NOT NULL,
    logic_version INTEGER NOT NULL,
    params_hash   TEXT NOT NULL,
    name          TEXT NOT NULL,
    rule_group    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    applies_to    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    description   TEXT NOT NULL,
    remediation   TEXT NOT NULL,
    parameters    JSON NOT NULL DEFAULT '{}',
    is_active     BOOLEAN NOT NULL,
    valid_from    TIMESTAMP NOT NULL,
    valid_to      TIMESTAMP,
    UNIQUE (rule_id, rule_version)
);


-- The calendar, 2000-2049: wide enough for any date a note can carry in `created`.
-- Names are English whatever the locale; day_of_week is ISO (1 = Monday).
CREATE TABLE dim_date (
    date_key     DATE PRIMARY KEY,
    year         INTEGER NOT NULL,
    quarter      INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    month_name   TEXT NOT NULL,
    iso_year     INTEGER NOT NULL,
    iso_week     INTEGER NOT NULL,
    day_of_week  INTEGER NOT NULL,
    day_name     TEXT NOT NULL,
    is_weekend   BOOLEAN NOT NULL
);
INSERT INTO dim_date
SELECT
    CAST(d AS DATE), year(d), quarter(d), month(d), monthname(d), isoyear(d), weekofyear(d), isodow(d), dayname(d), isodow(d) >= 6
FROM generate_series(DATE '2000-01-01', DATE '2049-12-31', INTERVAL 1 DAY) AS t(d);
