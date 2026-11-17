-- The event log, and the dimensions around it. The same as the DuckDB schema (see sql/duckdb/migrations/V11),
-- which explains what each column means.
--
-- The computed columns are PERSISTED, so that they can be indexed. DATETIME2 values are UTC. Texts that are keys
-- or are compared by value use the binary collation, like the keys of the other tables.

CREATE TABLE [{schema}].events (
    event_id      NVARCHAR(36)  COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    system        NVARCHAR(50)  COLLATE Latin1_General_100_BIN2 NOT NULL,
    category      NVARCHAR(50)  COLLATE Latin1_General_100_BIN2 NOT NULL CONSTRAINT ck_events_category CHECK (category IN (N'state', N'health', N'system')),
    event_type    NVARCHAR(100) COLLATE Latin1_General_100_BIN2 NOT NULL,
    event_version INT           NOT NULL,
    rule_id       NVARCHAR(50)  COLLATE Latin1_General_100_BIN2 NULL,
    entity_id     NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NULL,
    occurred_at   DATETIME2(6)  NOT NULL,
    inserted_at   DATETIME2(6)  NOT NULL CONSTRAINT df_events_inserted_at DEFAULT SYSUTCDATETIME(),
    occurred_date AS CAST(occurred_at AS DATE) PERSISTED,
    run_id        NVARCHAR(36)  COLLATE Latin1_General_100_BIN2 NULL,
    producer      NVARCHAR(100) NOT NULL,
    dedupe_key    NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL CONSTRAINT uq_events_dedupe_key UNIQUE,
    deleted_at    DATETIME2(6)  NULL,
    is_deleted    AS CAST(CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    payload       NVARCHAR(MAX) NOT NULL CONSTRAINT df_events_payload DEFAULT N'{}' CONSTRAINT ck_events_payload CHECK (ISJSON(payload) = 1)
);
GO

CREATE INDEX idx_events_type_time            ON [{schema}].events (event_type, occurred_at);
CREATE INDEX idx_events_entity_time          ON [{schema}].events (entity_id, occurred_at);
CREATE INDEX idx_events_system_category_time ON [{schema}].events (system, category, occurred_at);
CREATE INDEX idx_events_occurred_date        ON [{schema}].events (occurred_date);
CREATE INDEX idx_events_rule                 ON [{schema}].events (rule_id, occurred_at);
CREATE INDEX idx_events_run                  ON [{schema}].events (run_id);
GO


CREATE TABLE [{schema}].dim_severity (
    severity      NVARCHAR(20)  COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    severity_rank INT           NOT NULL,
    description   NVARCHAR(500) NOT NULL
);
GO
INSERT INTO [{schema}].dim_severity VALUES
    (N'info',  1, N'Worth knowing; nothing is wrong yet or the cost of ignoring it is small.'),
    (N'warn',  2, N'Something drifts from the conventions; fix it when convenient.'),
    (N'error', 3, N'Something is broken (an invalid value, a dangling link, a failed sync); fix it soon.');
GO


CREATE TABLE [{schema}].dim_event_type (
    event_type      NVARCHAR(100) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    system          NVARCHAR(50)  NOT NULL,
    category        NVARCHAR(50)  NOT NULL,
    current_version INT           NOT NULL,
    description     NVARCHAR(1000) NOT NULL
);
GO


CREATE TABLE [{schema}].dim_rule (
    rule_sk       INT            NOT NULL PRIMARY KEY,
    rule_id       NVARCHAR(50)   COLLATE Latin1_General_100_BIN2 NOT NULL,
    rule_version  INT            NOT NULL,
    logic_version INT            NOT NULL,
    params_hash   NVARCHAR(64)   NOT NULL,
    name          NVARCHAR(100)  NOT NULL,
    rule_group    NVARCHAR(50)   NOT NULL,
    kind          NVARCHAR(50)   NOT NULL,
    applies_to    NVARCHAR(50)   NOT NULL,
    severity      NVARCHAR(20)   NOT NULL,
    description   NVARCHAR(2000) NOT NULL,
    remediation   NVARCHAR(2000) NOT NULL,
    parameters    NVARCHAR(MAX)  NOT NULL CONSTRAINT df_dim_rule_parameters DEFAULT N'{}' CONSTRAINT ck_dim_rule_parameters CHECK (ISJSON(parameters) = 1),
    is_active     BIT            NOT NULL,
    valid_from    DATETIME2(6)   NOT NULL,
    valid_to      DATETIME2(6)   NULL,
    CONSTRAINT uq_dim_rule_version UNIQUE (rule_id, rule_version)
);
GO


CREATE TABLE [{schema}].dim_date (
    date_key     DATE         NOT NULL PRIMARY KEY,
    year         INT          NOT NULL,
    quarter      INT          NOT NULL,
    month        INT          NOT NULL,
    month_name   NVARCHAR(20) NOT NULL,
    iso_year     INT          NOT NULL,
    iso_week     INT          NOT NULL,
    day_of_week  INT          NOT NULL,
    day_name     NVARCHAR(20) NOT NULL,
    is_weekend   BIT          NOT NULL
);
GO

-- 1900-01-01 was a Monday, so the distance to it gives the ISO weekday whatever DATEFIRST or the language is.
WITH days AS (
    SELECT CAST('2000-01-01' AS DATE) AS d
    UNION ALL
    SELECT DATEADD(DAY, 1, d) FROM days WHERE d < '2049-12-31'
),
calendar AS (
    SELECT d, (DATEDIFF(DAY, '1900-01-01', d) % 7) + 1 AS dow FROM days
)
INSERT INTO [{schema}].dim_date
SELECT
    d, YEAR(d), DATEPART(QUARTER, d), MONTH(d),
    CHOOSE(MONTH(d), N'January', N'February', N'March', N'April', N'May', N'June',
                     N'July', N'August', N'September', N'October', N'November', N'December'),
    YEAR(DATEADD(DAY, 26 - DATEPART(ISO_WEEK, d), d)),   -- the year of the week's Thursday
    DATEPART(ISO_WEEK, d),
    dow,
    CHOOSE(dow, N'Monday', N'Tuesday', N'Wednesday', N'Thursday', N'Friday', N'Saturday', N'Sunday'),
    CAST(CASE WHEN dow >= 6 THEN 1 ELSE 0 END AS BIT)
FROM calendar
OPTION (MAXRECURSION 0);
GO
