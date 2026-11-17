-- Phase 4: the detectors of the other rules, severity that can change during an episode, and task events.
--
--   rule_limits        the limits of every rule in force, read from dim_rule: the one place a threshold lives. A rule's
--                      limits change when the configuration does (HEALTH__RULES), and the detectors follow.
--   health_detections  the violations of the structure, knowledge, cadence, task, system and event rules, one row each.
--   health_findings    (replaced) everything found right now: property_issues and health_detections, with the version and
--                      the severity of each finding. A detector may say a severity of its own; otherwise the rule's.
--   health_events, fact_violations, open_alerts (replaced) follow a violation's severity through its episode.
--   task_events        silver: the task events.
--   activity_daily     (replaced) also counts tasks.
--
-- Rules that are not here are not evaluated: S4 (type_folder_mismatch) needs the folder-to-type mapping, which is
-- a setting of the vault and not in the database; T2 (duplicate_task_id) is only known to the sync that sees the duplicates;
-- E1 (violation_escalation) is applied by the health check itself.

CREATE OR REPLACE VIEW rule_limits AS
SELECT
    rule_id,
    name                                                    AS rule_name,
    TRY_CAST(parameters ->> '$.days' AS INTEGER)            AS days,
    TRY_CAST(parameters ->> '$.hours' AS INTEGER)           AS hours,
    TRY_CAST(parameters ->> '$.count' AS INTEGER)           AS max_count,
    TRY_CAST(parameters ->> '$.percent' AS INTEGER)         AS max_percent,
    TRY_CAST(parameters ->> '$.error_after_days' AS INTEGER) AS error_after_days
FROM dim_rule
WHERE valid_to IS NULL;
COMMENT ON VIEW rule_limits IS 'The limits (days, hours, max_count, max_percent, error_after_days) of every rule in force, from dim_rule.parameters: what the detectors compare against.';


CREATE OR REPLACE VIEW health_detections AS
-- ---- structure
SELECT 'duplicate_note_name' AS rule_name, path AS entity_id, name AS detail, in_archive, type AS note_type, CAST(NULL AS VARCHAR) AS severity
FROM vault_notes
WHERE name IN (SELECT name FROM vault_notes GROUP BY name HAVING count(*) > 1)
UNION ALL
SELECT 'duplicate_content', path, substr(body_hash, 1, 12), in_archive, type, NULL
FROM vault_notes
WHERE NOT is_empty AND NOT in_archive AND body_hash IN (
    SELECT body_hash FROM vault_notes WHERE NOT is_empty AND NOT in_archive AND body_hash IS NOT NULL GROUP BY body_hash HAVING count(*) > 1)
UNION ALL
SELECT 'untyped_note', path, '', in_archive, type, NULL
FROM vault_notes
WHERE type IS NULL AND NOT in_archive
UNION ALL
SELECT 'unexpected_empty_note', path, '', in_archive, type, NULL
FROM vault_notes
WHERE is_empty AND NOT in_archive AND COALESCE(type, '') <> 'evergreen'
-- ---- knowledge processing
UNION ALL
SELECT 'evergreen_never_ankified', e.path, '', e.in_archive, e.type, NULL
FROM evergreen_notes AS e JOIN rule_limits AS l ON l.rule_name = 'evergreen_never_ankified'
WHERE NOT e.is_empty AND e.ankified_date IS NULL AND e.created IS NOT NULL AND e.created <= current_date - l.days
UNION ALL
SELECT 'evergreen_stale_anki', e.path, '', e.in_archive, e.type, NULL
FROM evergreen_notes AS e JOIN rule_limits AS l ON l.rule_name = 'evergreen_stale_anki'
WHERE NOT e.is_empty AND e.ankified_date IS NOT NULL
  AND e.modified_at::DATE > e.ankified_date AND e.modified_at::DATE <= current_date - l.days
UNION ALL
SELECT 'placeholder_aging', p.path, '', p.in_archive, p.type, NULL
FROM evergreen_placeholders AS p JOIN rule_limits AS l ON l.rule_name = 'placeholder_aging'
WHERE p.modified_at::DATE <= current_date - l.days
UNION ALL
SELECT 'anki_review_gap', 'anki', CAST(r.last_day AS VARCHAR), FALSE, NULL, NULL
FROM (SELECT max(review_day) AS last_day FROM anki_reviews) AS r JOIN rule_limits AS l ON l.rule_name = 'anki_review_gap'
WHERE r.last_day IS NOT NULL AND r.last_day <= current_date - l.days
-- ---- cadence
UNION ALL
SELECT 'review_overdue', path, review_cycle, in_archive, type, NULL
FROM review_schedule
WHERE is_overdue
UNION ALL
SELECT 'weekly_review_gap', path, CAST(uncovered_days AS VARCHAR) || ' days', in_archive, type, NULL
FROM weekly_reviews
WHERE uncovered_days > 0
UNION ALL
SELECT 'no_daily_note', 'daily_notes', CAST(d.last_day AS VARCHAR), FALSE, NULL, NULL
FROM (SELECT max(day) AS last_day FROM daily_notes WHERE NOT in_archive) AS d JOIN rule_limits AS l ON l.rule_name = 'no_daily_note'
WHERE d.last_day IS NOT NULL AND d.last_day <= current_date - l.days
UNION ALL
SELECT 'meeting_inbox_aging', n.path, '', n.in_archive, n.type, NULL
FROM vault_notes AS n JOIN rule_limits AS l ON l.rule_name = 'meeting_inbox_aging'
WHERE n.type = 'meeting' AND n.status = 'inbox' AND COALESCE(n.created, n.modified_at::DATE) <= current_date - l.days
UNION ALL
SELECT 'stale_active_project', p.path, '', p.in_archive, p.type, NULL
FROM project_notes AS p JOIN rule_limits AS l ON l.rule_name = 'stale_active_project'
WHERE p.status = 'active' AND p.modified_at::DATE <= current_date - l.days
UNION ALL
SELECT 'long_paused_project', p.path, '', p.in_archive, p.type, NULL
FROM project_notes AS p JOIN rule_limits AS l ON l.rule_name = 'long_paused_project'
WHERE p.status = 'paused' AND p.modified_at::DATE <= current_date - l.days
-- ---- tasks (the entity is the task id; the note it is in is the detail)
UNION ALL
SELECT 'task_overdue', t.task_id, t.note_path, t.in_archive, NULL,
       CASE WHEN t.days_overdue > l.error_after_days THEN 'error' ELSE 'warn' END
FROM vault_tasks AS t JOIN rule_limits AS l ON l.rule_name = 'task_overdue'
WHERE t.is_overdue
-- ---- system (what the log says about the syncs and the checks)
UNION ALL
SELECT 'sync_failed', system, '', FALSE, NULL, NULL
FROM (SELECT system, ok, row_number() OVER (PARTITION BY system ORDER BY finished_at DESC, event_id DESC) AS recent FROM fact_sync_runs)
WHERE recent = 1 AND NOT ok
UNION ALL
SELECT 'sync_stale', r.system, '', FALSE, NULL, NULL
FROM (SELECT system, max(finished_at) AS last_ok FROM fact_sync_runs WHERE ok GROUP BY system) AS r
JOIN rule_limits AS l ON l.rule_name = 'sync_stale'
WHERE r.last_ok < (now() AT TIME ZONE 'UTC') - to_hours(l.hours)
UNION ALL
SELECT 'mass_deletion', 'obsidian', r.run_id, FALSE, NULL, NULL
FROM (SELECT *, row_number() OVER (ORDER BY finished_at DESC, event_id DESC) AS recent FROM fact_sync_runs WHERE ok AND system = 'obsidian') AS r
JOIN rule_limits AS l ON l.rule_name = 'mass_deletion'
WHERE r.recent = 1 AND r.notes_removed > 0
  AND r.notes_removed * 100 > l.max_percent * (r.notes_removed + r.notes_unchanged + r.notes_updated)
UNION ALL
SELECT 'health_check_not_run', 'health', '', FALSE, NULL, NULL
FROM (SELECT max(occurred_at) AS last_check FROM events WHERE event_type = 'health.check_completed' AND NOT is_deleted) AS c
JOIN rule_limits AS l ON l.rule_name = 'health_check_not_run'
WHERE c.last_check < (now() AT TIME ZONE 'UTC') - to_hours(l.hours)
-- ---- questions asked of the log (the baseline is not a spike, and the system's own alerts are not counted)
UNION ALL
SELECT 'violation_spike', 'vault', '', FALSE, NULL, NULL
FROM (SELECT count(*) AS opened FROM health_events
      WHERE event_type = 'health.violation_opened' AND NOT is_baseline AND rule_id NOT LIKE 'E%' AND rule_id NOT LIKE 'Y%'
        AND occurred_at >= (now() AT TIME ZONE 'UTC') - to_hours((SELECT hours FROM rule_limits WHERE rule_name = 'violation_spike'))) AS s
WHERE s.opened > (SELECT max_count FROM rule_limits WHERE rule_name = 'violation_spike')
UNION ALL
SELECT 'violation_flapping', h.entity_id, h.rule_id || ': ' || h.detail, bool_or(h.in_archive), NULL, NULL
FROM health_events AS h
WHERE h.event_type = 'health.violation_opened' AND h.rule_id NOT LIKE 'E%' AND h.rule_id NOT LIKE 'Y%'
  AND h.occurred_at >= (now() AT TIME ZONE 'UTC') - to_days((SELECT days FROM rule_limits WHERE rule_name = 'violation_flapping'))
GROUP BY h.rule_id, h.entity_id, h.detail
HAVING count(*) >= (SELECT max_count FROM rule_limits WHERE rule_name = 'violation_flapping')
UNION ALL
SELECT 'mass_change', 'obsidian', r.run_id, FALSE, NULL, NULL
FROM (SELECT *, row_number() OVER (ORDER BY finished_at DESC, event_id DESC) AS recent FROM fact_sync_runs WHERE ok AND system = 'obsidian') AS r
JOIN rule_limits AS l ON l.rule_name = 'mass_change'
WHERE r.recent = 1 AND r.notes_updated > l.max_count
UNION ALL
SELECT 'activity_drought', 'vault', CAST(CAST(a.last_activity AS DATE) AS VARCHAR), FALSE, NULL, NULL
FROM (SELECT max(inserted_at) AS last_activity FROM note_events WHERE event_type IN ('note.created', 'note.updated') AND NOT is_baseline) AS a
JOIN rule_limits AS l ON l.rule_name = 'activity_drought'
WHERE a.last_activity < (now() AT TIME ZONE 'UTC') - to_days(l.days);
COMMENT ON VIEW health_detections IS 'The violations of the structure, knowledge, cadence, task, system and event rules found right now: rule_name, entity_id, detail, in_archive, note_type and a severity of its own when the detector has one. The limits come from rule_limits.';


CREATE OR REPLACE VIEW health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    d.entity_id,
    d.detail,
    d.in_archive,
    d.note_type,
    COALESCE(d.severity, r.severity) AS severity
FROM (
    SELECT rule AS rule_name, path AS entity_id, COALESCE(detail, '') AS detail, in_archive, type AS note_type, CAST(NULL AS VARCHAR) AS severity
    FROM property_issues
    UNION ALL
    SELECT rule_name, entity_id, detail, in_archive, note_type, severity FROM health_detections
) AS d
JOIN dim_rule AS r ON r.name = d.rule_name AND r.valid_to IS NULL;
COMMENT ON VIEW health_findings IS 'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it and its severity (the detector''s own, else the rule''s). The input of the health check.';


CREATE OR REPLACE VIEW health_events AS
SELECT
    event_id,
    event_type,
    rule_id,
    event_version,
    entity_id,
    COALESCE(payload ->> '$.detail', '')                    AS detail,
    COALESCE((payload ->> '$.in_archive') = 'true', FALSE)  AS in_archive,
    payload ->> '$.note_type'                               AS note_type,
    payload ->> '$.reason'                                  AS reason,
    payload ->> '$.severity'                                AS severity,
    COALESCE((payload ->> '$.baseline') = 'true', FALSE)    AS is_baseline,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    payload
FROM events
WHERE category = 'health' AND NOT is_deleted;
COMMENT ON VIEW health_events IS 'The health events (violation_opened, violation_resolved, severity_changed) with detail, in_archive, note_type, reason (why it was resolved or its severity changed), severity and is_baseline as columns.';


CREATE OR REPLACE VIEW fact_violations AS
WITH numbered AS (
    SELECT
        *,
        count(*) FILTER (WHERE event_type = 'health.violation_opened')
            OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id) AS episode_no
    FROM health_events
    WHERE event_type IN ('health.violation_opened', 'health.violation_resolved', 'health.severity_changed')
),
ranked AS (
    SELECT
        *,
        row_number() OVER (PARTITION BY rule_id, entity_id, detail, episode_no
                           ORDER BY CASE WHEN severity IS NULL THEN 1 ELSE 0 END, occurred_at DESC, event_id DESC) AS latest_severity
    FROM numbered
    WHERE episode_no >= 1
),
episodes AS (
    SELECT
        rule_id, entity_id, detail, episode_no,
        min(event_id)       FILTER (WHERE event_type = 'health.violation_opened')   AS violation_id,
        min(event_version)  FILTER (WHERE event_type = 'health.violation_opened')   AS event_version,
        bool_or(in_archive) FILTER (WHERE event_type = 'health.violation_opened')   AS in_archive,
        min(note_type)      FILTER (WHERE event_type = 'health.violation_opened')   AS note_type,
        bool_or(is_baseline) FILTER (WHERE event_type = 'health.violation_opened')  AS is_baseline,
        min(occurred_at)    FILTER (WHERE event_type = 'health.violation_opened')   AS opened_at,
        min(occurred_at)    FILTER (WHERE event_type = 'health.violation_resolved') AS resolved_at,
        min(reason)         FILTER (WHERE event_type = 'health.violation_resolved') AS resolution,
        min(severity)       FILTER (WHERE latest_severity = 1)                      AS severity
    FROM ranked
    GROUP BY rule_id, entity_id, detail, episode_no
)
SELECT
    e.violation_id,
    e.rule_id,
    r.rule_sk,
    COALESCE(e.severity, r.severity)                                            AS severity,
    e.entity_id,
    e.detail,
    e.in_archive,
    e.note_type,
    e.is_baseline,
    e.opened_at,
    CAST(e.opened_at AS DATE)                                                   AS opened_date,
    e.resolved_at,
    e.resolution,
    e.resolved_at IS NULL                                                       AS is_open,
    date_diff('day', e.opened_at, COALESCE(e.resolved_at, now() AT TIME ZONE 'UTC')) AS days_open,
    e.episode_no - 1                                                            AS reopen_count
FROM episodes AS e
LEFT JOIN dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version
WHERE e.violation_id IS NOT NULL;
COMMENT ON VIEW fact_violations IS 'One row per violation episode: opened_at, resolved_at (NULL while open), resolution (fixed or note_gone), severity (the latest of the episode: it can escalate), days_open (until now while open; a lower bound for is_baseline episodes), reopen_count (0 for the first episode of a violation). Keys: rule_sk (dim_rule), severity (dim_severity), opened_date (dim_date), entity_id (dim_note for notes).';


CREATE OR REPLACE VIEW open_alerts AS
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
FROM fact_violations AS v
JOIN dim_rule AS r ON r.rule_sk = v.rule_sk
JOIN dim_severity AS s ON s.severity = v.severity
LEFT JOIN vault_notes AS n ON n.path = v.entity_id
WHERE v.is_open AND NOT v.in_archive;
COMMENT ON VIEW open_alerts IS 'The open violations of the active vault (not the archive), with their current severity, how long they have been open and what the rule says about them. Sort by severity_rank DESC, days_open DESC.';


CREATE OR REPLACE VIEW task_events AS
SELECT
    event_id,
    event_type,
    entity_id                                       AS task_id,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    event_type = 'task.discovered'                  AS is_baseline,
    payload ->> '$.note_path'                       AS note_path,
    payload ->> '$.state'                           AS state,
    payload ->> '$.previous_state'                  AS previous_state,
    payload ->> '$.due_date'                        AS due_date,
    payload ->> '$.priority'                        AS priority,
    payload -> '$.changed'                          AS changed,
    payload
FROM events
WHERE system = 'obsidian' AND event_type LIKE 'task.%' AND NOT is_deleted;
COMMENT ON VIEW task_events IS 'The events about tasks (discovered, created, updated, completed, deleted) with note_path, state, previous_state, due_date and priority as columns. Never the text of a task.';


CREATE OR REPLACE VIEW activity_daily AS
WITH notes AS (
    SELECT
        occurred_date,
        count(*) FILTER (WHERE event_type = 'note.created')  AS notes_created,
        count(*) FILTER (WHERE event_type = 'note.updated')  AS notes_updated,
        count(*) FILTER (WHERE event_type = 'note.moved')    AS notes_moved,
        count(*) FILTER (WHERE event_type = 'note.deleted')  AS notes_deleted,
        count(DISTINCT path)                                 AS notes_touched
    FROM note_events
    WHERE NOT is_baseline
    GROUP BY occurred_date
),
tasks AS (
    SELECT
        occurred_date,
        count(*) FILTER (WHERE event_type = 'task.created')   AS tasks_created,
        count(*) FILTER (WHERE event_type = 'task.completed') AS tasks_completed
    FROM task_events
    WHERE NOT is_baseline
    GROUP BY occurred_date
)
SELECT
    COALESCE(n.occurred_date, t.occurred_date)   AS occurred_date,
    COALESCE(n.notes_created, 0)                 AS notes_created,
    COALESCE(n.notes_updated, 0)                 AS notes_updated,
    COALESCE(n.notes_moved, 0)                   AS notes_moved,
    COALESCE(n.notes_deleted, 0)                 AS notes_deleted,
    COALESCE(n.notes_touched, 0)                 AS notes_touched,
    COALESCE(t.tasks_created, 0)                 AS tasks_created,
    COALESCE(t.tasks_completed, 0)               AS tasks_completed
FROM notes AS n
FULL OUTER JOIN tasks AS t ON t.occurred_date = n.occurred_date;
COMMENT ON VIEW activity_daily IS 'Per day: notes created, updated, moved and deleted, how many different notes that touched, and tasks created and completed. Baseline (discovered) events are not counted.';
