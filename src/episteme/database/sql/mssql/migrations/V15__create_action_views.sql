-- The same views, with the same names and columns, as in the DuckDB schema (see sql/duckdb/migrations/V15), in T-SQL.

CREATE OR ALTER VIEW [{schema}].action_events AS
SELECT
    event_id,
    event_type,
    SUBSTRING(event_type, 8, LEN(event_type))                      AS action,
    entity_id,
    occurred_at,
    occurred_date,
    inserted_at,
    JSON_VALUE(payload, N'$.note_path')                            AS note_path,
    JSON_VALUE(payload, N'$.state')                                AS state,
    JSON_VALUE(payload, N'$.template')                             AS template,
    JSON_VALUE(payload, N'$.type')                                 AS note_type,
    TRY_CAST(JSON_VALUE(payload, N'$.notes') AS BIGINT)            AS notes,
    payload
FROM [{schema}].events
WHERE event_type LIKE N'action.%' AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'action_events', N'What Episteme did itself (note_created, task_added, task_updated, task_completed, task_deleted, snapshot_taken, data_cleared) with the note, state, template, type and notes count as columns: not what a sync saw happen.';
GO


CREATE OR ALTER VIEW [{schema}].alert_deliveries AS
SELECT
    event_id,
    entity_id                                                                   AS violation_id,
    CAST(CASE WHEN event_type = N'alert.delivered' THEN 1 ELSE 0 END AS BIT)    AS ok,
    JSON_VALUE(payload, N'$.sink')                                              AS sink,
    JSON_VALUE(payload, N'$.kind')                                              AS kind,
    JSON_VALUE(payload, N'$.severity')                                          AS severity,
    JSON_VALUE(payload, N'$.rule_id')                                           AS rule_id,
    JSON_VALUE(payload, N'$.error')                                             AS error,
    occurred_at,
    occurred_date,
    inserted_at
FROM [{schema}].events
WHERE event_type IN (N'alert.delivered', N'alert.delivery_failed') AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'alert_deliveries', N'Every alert sent to a sink (ok) and every attempt that failed (error): which violation, to which sink, at which severity. Join violation_id to fact_violations.';
GO
