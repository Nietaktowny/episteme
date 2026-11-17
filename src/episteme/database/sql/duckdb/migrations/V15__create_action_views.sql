-- What Episteme did, and what it sent where.
--
--   action_events    silver  the things Episteme itself did (created a note, changed a task, took a snapshot, cleared the
--                            database), as opposed to the changes a sync saw. A note created by Episteme is an action.note_created
--                            now and a note.created at the next sync: the first is who did it, the second is what the vault shows.
--   alert_deliveries silver  every alert sent to a sink, and every attempt that failed.

CREATE OR REPLACE VIEW action_events AS
SELECT
    event_id,
    event_type,
    substr(event_type, 8)                       AS action,
    entity_id,
    occurred_at,
    occurred_date,
    inserted_at,
    payload ->> '$.note_path'                   AS note_path,
    payload ->> '$.state'                       AS state,
    payload ->> '$.template'                    AS template,
    payload ->> '$.type'                        AS note_type,
    TRY_CAST(payload ->> '$.notes' AS BIGINT)   AS notes,
    payload
FROM events
WHERE event_type LIKE 'action.%' AND NOT is_deleted;
COMMENT ON VIEW action_events IS 'What Episteme did itself (note_created, task_added, task_updated, task_completed, task_deleted, snapshot_taken, data_cleared) with the note, state, template, type and notes count as columns: not what a sync saw happen.';


CREATE OR REPLACE VIEW alert_deliveries AS
SELECT
    event_id,
    entity_id                                    AS violation_id,
    event_type = 'alert.delivered'               AS ok,
    payload ->> '$.sink'                         AS sink,
    payload ->> '$.kind'                         AS kind,
    payload ->> '$.severity'                     AS severity,
    payload ->> '$.rule_id'                      AS rule_id,
    payload ->> '$.error'                        AS error,
    occurred_at,
    occurred_date,
    inserted_at
FROM events
WHERE event_type IN ('alert.delivered', 'alert.delivery_failed') AND NOT is_deleted;
COMMENT ON VIEW alert_deliveries IS 'Every alert sent to a sink (ok) and every attempt that failed (error): which violation, to which sink, at which severity. Join violation_id to fact_violations.';
