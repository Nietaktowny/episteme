-- Views over the event log: silver (the payload's fields promoted to columns) and gold (facts of a star schema).
-- Every view leaves out soft-deleted events. The dimensions are the dim_* tables of V11 and dim_note below.
--
--   note_events      silver  one row per note event, with what changed as columns
--   fact_events      gold    one row per event: the transaction fact; joins to dim_date, dim_event_type, dim_rule, dim_severity
--   fact_sync_runs   gold    one row per sync, succeeded or failed
--   activity_daily   gold    what happened to the notes, per day
--   dim_note         gold    the notes as they are now: the dimension of entity_id for note events
--
-- A field moves from a payload into a column of a view here when it is asked for; the next step, when everything asks
-- for it, is a column of `events` itself (as `type` and `created` became columns of `notes`).

-- ---------------------------------------------------------------------------
-- Silver: the note events.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW note_events AS
SELECT
    event_id,
    event_type,
    entity_id                                       AS path,
    starts_with(entity_id, '40_Archive/')           AS in_archive,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    event_version,
    event_type = 'note.discovered'                  AS is_baseline,        -- there when the log started: not a creation
    payload ->> '$.type'                            AS note_type,
    payload ->> '$.category_directory'              AS category_directory,
    TRY_CAST(payload ->> '$.size' AS BIGINT)        AS size,
    payload ->> '$.occurred_basis'                  AS occurred_basis,
    payload ->> '$.from'                            AS moved_from,
    TRY_CAST(payload ->> '$.size_delta' AS BIGINT)  AS size_delta,
    payload -> '$.changed'                          AS changed,
    payload -> '$.changed_keys'                     AS changed_keys,
    payload ->> '$.properties.type[0]'              AS type_before,
    payload ->> '$.properties.type[1]'              AS type_after,
    payload ->> '$.properties.status[0]'            AS status_before,
    payload ->> '$.properties.status[1]'            AS status_after,
    payload
FROM events
WHERE system = 'obsidian' AND event_type LIKE 'note.%' AND NOT is_deleted;
COMMENT ON VIEW note_events IS 'The events about notes (discovered, created, updated, moved, deleted) with the interesting payload fields as columns: note_type, moved_from, size_delta, what changed, and the old and new type and status. is_baseline marks note.discovered: a note that was there when the log started.';


-- ---------------------------------------------------------------------------
-- Gold: the dimension of the notes, as they are now.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW dim_note AS
SELECT path, name, type, folder, category_directory, in_archive, is_empty, status, created
FROM vault_notes;
COMMENT ON VIEW dim_note IS 'The notes as they are now, for joining note events and violations on path = entity_id. A note that is gone has events but no row here.';


-- ---------------------------------------------------------------------------
-- Gold: the transaction fact. One row per event, with its rule (as it was when the event was written) and severity.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW fact_events AS
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
FROM events AS e
LEFT JOIN dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version
WHERE NOT e.is_deleted;
COMMENT ON VIEW fact_events IS 'One row per event. Keys: occurred_date (dim_date), event_type (dim_event_type), rule_sk (dim_rule, for health events, the version in force when the event was written), severity (dim_severity), entity_id (dim_note for notes).';


-- ---------------------------------------------------------------------------
-- Gold: one row per sync.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW fact_sync_runs AS
SELECT
    event_id,
    run_id,
    system,
    occurred_at                                              AS finished_at,
    occurred_date,
    inserted_at,
    event_type = 'sync.completed'                            AS ok,
    TRY_CAST(payload ->> '$.changed' AS BIGINT)              AS items_changed,
    TRY_CAST(payload ->> '$.added' AS BIGINT)                AS notes_added,
    TRY_CAST(payload ->> '$.updated' AS BIGINT)              AS notes_updated,
    TRY_CAST(payload ->> '$.unchanged' AS BIGINT)            AS notes_unchanged,
    TRY_CAST(payload ->> '$.removed' AS BIGINT)              AS notes_removed,
    TRY_CAST(payload ->> '$.tasks_added' AS BIGINT)          AS tasks_added,
    TRY_CAST(payload ->> '$.tasks_updated' AS BIGINT)        AS tasks_updated,
    TRY_CAST(payload ->> '$.tasks_removed' AS BIGINT)        AS tasks_removed,
    TRY_CAST(payload ->> '$.reviews_added' AS BIGINT)        AS reviews_added,
    TRY_CAST(payload ->> '$.mapping_seconds' AS DOUBLE)      AS mapping_seconds,
    TRY_CAST(payload ->> '$.database_seconds' AS DOUBLE)     AS database_seconds,
    payload ->> '$.error'                                    AS error,
    payload ->> '$.message'                                  AS message
FROM events
WHERE event_type IN ('sync.completed', 'sync.failed') AND NOT is_deleted;
COMMENT ON VIEW fact_sync_runs IS 'One row per sync of a system, succeeded (ok) or failed (error, message). The counts are those of the system: notes and tasks for obsidian, items_changed and reviews_added for anki.';


-- ---------------------------------------------------------------------------
-- Gold: what happened to the notes, per day of occurred_at. note.discovered is left out: it is not an activity.
-- A note created on the day its frontmatter says counts on that day, not on the day it was synced.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW activity_daily AS
SELECT
    occurred_date,
    count(*) FILTER (WHERE event_type = 'note.created')  AS notes_created,
    count(*) FILTER (WHERE event_type = 'note.updated')  AS notes_updated,
    count(*) FILTER (WHERE event_type = 'note.moved')    AS notes_moved,
    count(*) FILTER (WHERE event_type = 'note.deleted')  AS notes_deleted,
    count(DISTINCT path)                                 AS notes_touched
FROM note_events
WHERE NOT is_baseline
GROUP BY occurred_date;
COMMENT ON VIEW activity_daily IS 'Per day: notes created, updated, moved and deleted, and how many different notes that touched. Baseline (note.discovered) events are not counted.';
