-- Views over the event log. The same views, with the same names and columns, as in the DuckDB schema
-- (see sql/duckdb/migrations/V12, which says what each is for), written in T-SQL: a flag is a BIT, and the
-- payload is read with JSON_VALUE / JSON_QUERY.

CREATE OR ALTER VIEW [{schema}].note_events AS
SELECT
    event_id,
    event_type,
    entity_id                                                                       AS path,
    CAST(CASE WHEN entity_id LIKE N'40\_Archive/%' ESCAPE N'\' THEN 1 ELSE 0 END AS BIT) AS in_archive,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    event_version,
    CAST(CASE WHEN event_type = N'note.discovered' THEN 1 ELSE 0 END AS BIT)        AS is_baseline,
    JSON_VALUE(payload, N'$.type')                                                  AS note_type,
    JSON_VALUE(payload, N'$.category_directory')                                    AS category_directory,
    TRY_CAST(JSON_VALUE(payload, N'$.size') AS BIGINT)                              AS size,
    JSON_VALUE(payload, N'$.occurred_basis')                                        AS occurred_basis,
    JSON_VALUE(payload, N'$."from"')                                                AS moved_from,
    TRY_CAST(JSON_VALUE(payload, N'$.size_delta') AS BIGINT)                        AS size_delta,
    JSON_QUERY(payload, N'$.changed')                                               AS changed,
    JSON_QUERY(payload, N'$.changed_keys')                                          AS changed_keys,
    JSON_VALUE(payload, N'$.properties.type[0]')                                    AS type_before,
    JSON_VALUE(payload, N'$.properties.type[1]')                                    AS type_after,
    JSON_VALUE(payload, N'$.properties.status[0]')                                  AS status_before,
    JSON_VALUE(payload, N'$.properties.status[1]')                                  AS status_after,
    payload
FROM [{schema}].events
WHERE system = N'obsidian' AND event_type LIKE N'note.%' AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'note_events', N'The events about notes (discovered, created, updated, moved, deleted) with the interesting payload fields as columns: note_type, moved_from, size_delta, what changed, and the old and new type and status. is_baseline marks note.discovered: a note that was there when the log started.';
GO


CREATE OR ALTER VIEW [{schema}].dim_note AS
SELECT path, name, type, folder, category_directory, in_archive, is_empty, status, created
FROM [{schema}].vault_notes;
GO
EXEC [{schema}].episteme_describe_view N'dim_note', N'The notes as they are now, for joining note events and violations on path = entity_id. A note that is gone has events but no row here.';
GO


CREATE OR ALTER VIEW [{schema}].fact_events AS
SELECT
    e.event_id,
    e.occurred_date,
    e.occurred_at,
    e.inserted_at,
    e.system,
    e.category,
    e.event_type,
    e.event_version,
    e.entity_id,
    e.rule_id,
    r.rule_sk,
    r.severity,
    e.run_id,
    e.producer,
    e.payload
FROM [{schema}].events AS e
LEFT JOIN [{schema}].dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version
WHERE e.is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'fact_events', N'One row per event. Keys: occurred_date (dim_date), event_type (dim_event_type), rule_sk (dim_rule, for health events, the version in force when the event was written), severity (dim_severity), entity_id (dim_note for notes).';
GO


CREATE OR ALTER VIEW [{schema}].fact_sync_runs AS
SELECT
    event_id,
    run_id,
    system,
    occurred_at                                                       AS finished_at,
    occurred_date,
    inserted_at,
    CAST(CASE WHEN event_type = N'sync.completed' THEN 1 ELSE 0 END AS BIT) AS ok,
    TRY_CAST(JSON_VALUE(payload, N'$.changed') AS BIGINT)             AS items_changed,
    TRY_CAST(JSON_VALUE(payload, N'$.added') AS BIGINT)               AS notes_added,
    TRY_CAST(JSON_VALUE(payload, N'$.updated') AS BIGINT)             AS notes_updated,
    TRY_CAST(JSON_VALUE(payload, N'$.unchanged') AS BIGINT)           AS notes_unchanged,
    TRY_CAST(JSON_VALUE(payload, N'$.removed') AS BIGINT)             AS notes_removed,
    TRY_CAST(JSON_VALUE(payload, N'$.tasks_added') AS BIGINT)         AS tasks_added,
    TRY_CAST(JSON_VALUE(payload, N'$.tasks_updated') AS BIGINT)       AS tasks_updated,
    TRY_CAST(JSON_VALUE(payload, N'$.tasks_removed') AS BIGINT)       AS tasks_removed,
    TRY_CAST(JSON_VALUE(payload, N'$.reviews_added') AS BIGINT)       AS reviews_added,
    TRY_CAST(JSON_VALUE(payload, N'$.mapping_seconds') AS FLOAT)      AS mapping_seconds,
    TRY_CAST(JSON_VALUE(payload, N'$.database_seconds') AS FLOAT)     AS database_seconds,
    JSON_VALUE(payload, N'$.error')                                   AS error,
    JSON_VALUE(payload, N'$.message')                                 AS message
FROM [{schema}].events
WHERE event_type IN (N'sync.completed', N'sync.failed') AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'fact_sync_runs', N'One row per sync of a system, succeeded (ok) or failed (error, message). The counts are those of the system: notes and tasks for obsidian, items_changed and reviews_added for anki.';
GO


CREATE OR ALTER VIEW [{schema}].activity_daily AS
SELECT
    occurred_date,
    COUNT(CASE WHEN event_type = N'note.created' THEN 1 END)  AS notes_created,
    COUNT(CASE WHEN event_type = N'note.updated' THEN 1 END)  AS notes_updated,
    COUNT(CASE WHEN event_type = N'note.moved'   THEN 1 END)  AS notes_moved,
    COUNT(CASE WHEN event_type = N'note.deleted' THEN 1 END)  AS notes_deleted,
    COUNT(DISTINCT path)                                      AS notes_touched
FROM [{schema}].note_events
WHERE is_baseline = 0
GROUP BY occurred_date;
GO
EXEC [{schema}].episteme_describe_view N'activity_daily', N'Per day: notes created, updated, moved and deleted, and how many different notes that touched. Baseline (note.discovered) events are not counted.';
GO
