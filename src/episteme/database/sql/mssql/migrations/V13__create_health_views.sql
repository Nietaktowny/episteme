-- The health lifecycle views. The same views, with the same names and columns, as in the DuckDB schema
-- (see sql/duckdb/migrations/V13, which explains them), written in T-SQL.

CREATE OR ALTER VIEW [{schema}].health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    p.path                   AS entity_id,
    COALESCE(p.detail, N'')  AS detail,
    p.in_archive,
    p.type                   AS note_type
FROM [{schema}].property_issues AS p
JOIN [{schema}].dim_rule AS r ON r.name = p.[rule] COLLATE Latin1_General_100_BIN2 AND r.rule_group = N'registry' AND r.valid_to IS NULL;
GO
EXEC [{schema}].episteme_describe_view N'health_findings', N'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it. The input of the health check; the registry rules (R1-R19) for now.';
GO


CREATE OR ALTER VIEW [{schema}].health_events AS
SELECT
    event_id,
    event_type,
    rule_id,
    event_version,
    entity_id,
    COALESCE(JSON_VALUE(payload, N'$.detail'), N'')                                         AS detail,
    CAST(CASE WHEN JSON_VALUE(payload, N'$.in_archive') = N'true' THEN 1 ELSE 0 END AS BIT) AS in_archive,
    JSON_VALUE(payload, N'$.note_type')                                                     AS note_type,
    JSON_VALUE(payload, N'$.reason')                                                        AS reason,
    CAST(CASE WHEN JSON_VALUE(payload, N'$.baseline') = N'true' THEN 1 ELSE 0 END AS BIT)  AS is_baseline,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    payload
FROM [{schema}].events
WHERE category = N'health' AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'health_events', N'The health events (violation_opened, violation_resolved) with detail, in_archive, note_type, reason (why it was resolved: fixed or note_gone) and is_baseline as columns.';
GO


CREATE OR ALTER VIEW [{schema}].fact_violations AS
WITH sequence AS (
    SELECT
        *,
        LEAD(event_type) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id)  AS next_type,
        LEAD(occurred_at) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id) AS next_at,
        LEAD(reason) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id)      AS next_reason,
        SUM(CASE WHEN event_type = N'health.violation_opened' THEN 1 ELSE 0 END)
            OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id ROWS UNBOUNDED PRECEDING) - 1 AS reopen_count
    FROM [{schema}].health_events
),
episodes AS (
    SELECT
        event_id, rule_id, event_version, entity_id, detail, in_archive, note_type, is_baseline, reopen_count,
        occurred_at AS opened_at,
        occurred_date AS opened_date,
        CASE WHEN next_type = N'health.violation_resolved' THEN next_at END     AS resolved_at,
        CASE WHEN next_type = N'health.violation_resolved' THEN next_reason END AS resolution
    FROM sequence
    WHERE event_type = N'health.violation_opened'
)
SELECT
    e.event_id                                                       AS violation_id,
    e.rule_id,
    r.rule_sk,
    r.severity,
    e.entity_id,
    e.detail,
    e.in_archive,
    e.note_type,
    e.is_baseline,
    e.opened_at,
    e.opened_date,
    e.resolved_at,
    e.resolution,
    CAST(CASE WHEN e.resolved_at IS NULL THEN 1 ELSE 0 END AS BIT)   AS is_open,
    DATEDIFF(DAY, e.opened_at, COALESCE(e.resolved_at, SYSUTCDATETIME())) AS days_open,
    e.reopen_count
FROM episodes AS e
LEFT JOIN [{schema}].dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version;
GO
EXEC [{schema}].episteme_describe_view N'fact_violations', N'One row per violation episode: opened_at, resolved_at (NULL while open), resolution (fixed or note_gone), days_open (until now while open; a lower bound for is_baseline episodes), reopen_count (0 for the first episode of a violation). Keys: rule_sk (dim_rule), severity (dim_severity), opened_date (dim_date), entity_id (dim_note).';
GO


CREATE OR ALTER VIEW [{schema}].open_alerts AS
SELECT
    v.violation_id,
    v.rule_id,
    r.name           AS rule_name,
    v.severity,
    s.severity_rank,
    v.entity_id,
    n.name           AS note_name,
    v.note_type,
    v.detail,
    v.opened_at,
    v.days_open,
    v.reopen_count,
    v.is_baseline,
    r.description,
    r.remediation
FROM [{schema}].fact_violations AS v
JOIN [{schema}].dim_rule AS r ON r.rule_sk = v.rule_sk
JOIN [{schema}].dim_severity AS s ON s.severity = v.severity COLLATE Latin1_General_100_BIN2
LEFT JOIN [{schema}].vault_notes AS n ON n.path = v.entity_id
WHERE v.is_open = 1 AND v.in_archive = 0;
GO
EXEC [{schema}].episteme_describe_view N'open_alerts', N'The open violations of the active vault (not the archive), with severity, how long they have been open and what the rule says about them. Sort by severity_rank DESC, days_open DESC.';
GO
