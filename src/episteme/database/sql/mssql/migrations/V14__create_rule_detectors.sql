-- Phase 4 views. The same views, with the same names and columns, as in the DuckDB schema (see
-- sql/duckdb/migrations/V14, which explains them), written in T-SQL. "Today" is [{schema}].today() (UTC).
-- Text columns that are joined or concatenated across arms are given the binary collation explicitly, like the keys.

CREATE OR ALTER VIEW [{schema}].rule_limits AS
SELECT
    rule_id,
    name                                                           AS rule_name,
    TRY_CAST(JSON_VALUE(parameters, N'$.days') AS INT)             AS days,
    TRY_CAST(JSON_VALUE(parameters, N'$.hours') AS INT)            AS hours,
    TRY_CAST(JSON_VALUE(parameters, N'$.count') AS INT)            AS max_count,
    TRY_CAST(JSON_VALUE(parameters, N'$.percent') AS INT)          AS max_percent,
    TRY_CAST(JSON_VALUE(parameters, N'$.error_after_days') AS INT) AS error_after_days
FROM [{schema}].dim_rule
WHERE valid_to IS NULL;
GO
EXEC [{schema}].episteme_describe_view N'rule_limits', N'The limits (days, hours, max_count, max_percent, error_after_days) of every rule in force, from dim_rule.parameters: what the detectors compare against.';
GO


CREATE OR ALTER VIEW [{schema}].health_detections AS
-- ---- structure
SELECT N'duplicate_note_name' AS rule_name, path COLLATE Latin1_General_100_BIN2 AS entity_id, name COLLATE Latin1_General_100_BIN2 AS detail, in_archive, type COLLATE Latin1_General_100_BIN2 AS note_type, CAST(NULL AS NVARCHAR(20)) COLLATE Latin1_General_100_BIN2 AS severity
FROM [{schema}].vault_notes
WHERE name IN (SELECT name FROM [{schema}].vault_notes GROUP BY name HAVING COUNT(*) > 1)
UNION ALL
SELECT N'duplicate_content', path, LEFT(body_hash, 12), in_archive, type, NULL
FROM [{schema}].vault_notes
WHERE is_empty = 0 AND in_archive = 0 AND body_hash IN (
    SELECT body_hash FROM [{schema}].vault_notes WHERE is_empty = 0 AND in_archive = 0 AND body_hash IS NOT NULL GROUP BY body_hash HAVING COUNT(*) > 1)
UNION ALL
SELECT N'untyped_note', path, N'', in_archive, type, NULL
FROM [{schema}].vault_notes
WHERE type IS NULL AND in_archive = 0
UNION ALL
SELECT N'unexpected_empty_note', path, N'', in_archive, type, NULL
FROM [{schema}].vault_notes
WHERE is_empty = 1 AND in_archive = 0 AND COALESCE(type, N'') <> N'evergreen'
-- ---- knowledge processing
UNION ALL
SELECT N'evergreen_never_ankified', e.path, N'', e.in_archive, e.type, NULL
FROM [{schema}].evergreen_notes AS e JOIN [{schema}].rule_limits AS l ON l.rule_name = N'evergreen_never_ankified'
WHERE e.is_empty = 0 AND e.ankified_date IS NULL AND e.created IS NOT NULL AND e.created <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'evergreen_stale_anki', e.path, N'', e.in_archive, e.type, NULL
FROM [{schema}].evergreen_notes AS e JOIN [{schema}].rule_limits AS l ON l.rule_name = N'evergreen_stale_anki'
WHERE e.is_empty = 0 AND e.ankified_date IS NOT NULL
  AND CAST(e.modified_at AS DATE) > e.ankified_date AND CAST(e.modified_at AS DATE) <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'placeholder_aging', p.path, N'', p.in_archive, p.type, NULL
FROM [{schema}].evergreen_placeholders AS p JOIN [{schema}].rule_limits AS l ON l.rule_name = N'placeholder_aging'
WHERE CAST(p.modified_at AS DATE) <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'anki_review_gap', N'anki', CAST(r.last_day AS NVARCHAR(10)), CAST(0 AS BIT), NULL, NULL
FROM (SELECT MAX(review_day) AS last_day FROM [{schema}].anki_reviews) AS r JOIN [{schema}].rule_limits AS l ON l.rule_name = N'anki_review_gap'
WHERE r.last_day IS NOT NULL AND r.last_day <= DATEADD(DAY, -l.days, [{schema}].today())
-- ---- cadence
UNION ALL
SELECT N'review_overdue', path, review_cycle, in_archive, type, NULL
FROM [{schema}].review_schedule
WHERE is_overdue = 1
UNION ALL
SELECT N'weekly_review_gap', path, CAST(uncovered_days AS NVARCHAR(20)) + N' days', in_archive, type, NULL
FROM [{schema}].weekly_reviews
WHERE uncovered_days > 0
UNION ALL
SELECT N'no_daily_note', N'daily_notes', CAST(d.last_day AS NVARCHAR(10)), CAST(0 AS BIT), NULL, NULL
FROM (SELECT MAX(day) AS last_day FROM [{schema}].daily_notes WHERE in_archive = 0) AS d JOIN [{schema}].rule_limits AS l ON l.rule_name = N'no_daily_note'
WHERE d.last_day IS NOT NULL AND d.last_day <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'meeting_inbox_aging', n.path, N'', n.in_archive, n.type, NULL
FROM [{schema}].vault_notes AS n JOIN [{schema}].rule_limits AS l ON l.rule_name = N'meeting_inbox_aging'
WHERE n.type = N'meeting' AND n.status = N'inbox' AND COALESCE(n.created, CAST(n.modified_at AS DATE)) <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'stale_active_project', p.path, N'', p.in_archive, p.type, NULL
FROM [{schema}].project_notes AS p JOIN [{schema}].rule_limits AS l ON l.rule_name = N'stale_active_project'
WHERE p.status = N'active' AND CAST(p.modified_at AS DATE) <= DATEADD(DAY, -l.days, [{schema}].today())
UNION ALL
SELECT N'long_paused_project', p.path, N'', p.in_archive, p.type, NULL
FROM [{schema}].project_notes AS p JOIN [{schema}].rule_limits AS l ON l.rule_name = N'long_paused_project'
WHERE p.status = N'paused' AND CAST(p.modified_at AS DATE) <= DATEADD(DAY, -l.days, [{schema}].today())
-- ---- tasks (the entity is the task id; the note it is in is the detail)
UNION ALL
SELECT N'task_overdue', t.task_id, t.note_path, t.in_archive, NULL,
       CASE WHEN t.days_overdue > l.error_after_days THEN N'error' ELSE N'warn' END
FROM [{schema}].vault_tasks AS t JOIN [{schema}].rule_limits AS l ON l.rule_name = N'task_overdue'
WHERE t.is_overdue = 1
-- ---- system (what the log says about the syncs and the checks)
UNION ALL
SELECT N'sync_failed', system, N'', CAST(0 AS BIT), NULL, NULL
FROM (SELECT system, ok, ROW_NUMBER() OVER (PARTITION BY system ORDER BY finished_at DESC, event_id DESC) AS recent FROM [{schema}].fact_sync_runs) AS s
WHERE recent = 1 AND ok = 0
UNION ALL
SELECT N'sync_stale', r.system, N'', CAST(0 AS BIT), NULL, NULL
FROM (SELECT system, MAX(finished_at) AS last_ok FROM [{schema}].fact_sync_runs WHERE ok = 1 GROUP BY system) AS r
JOIN [{schema}].rule_limits AS l ON l.rule_name = N'sync_stale'
WHERE r.last_ok < DATEADD(HOUR, -l.hours, SYSUTCDATETIME())
UNION ALL
SELECT N'mass_deletion', N'obsidian', r.run_id, CAST(0 AS BIT), NULL, NULL
FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY finished_at DESC, event_id DESC) AS recent FROM [{schema}].fact_sync_runs WHERE ok = 1 AND system = N'obsidian') AS r
JOIN [{schema}].rule_limits AS l ON l.rule_name = N'mass_deletion'
WHERE r.recent = 1 AND r.notes_removed > 0
  AND r.notes_removed * 100 > l.max_percent * (r.notes_removed + r.notes_unchanged + r.notes_updated)
UNION ALL
SELECT N'health_check_not_run', N'health', N'', CAST(0 AS BIT), NULL, NULL
FROM (SELECT MAX(occurred_at) AS last_check FROM [{schema}].events WHERE event_type = N'health.check_completed' AND is_deleted = 0) AS c
JOIN [{schema}].rule_limits AS l ON l.rule_name = N'health_check_not_run'
WHERE c.last_check < DATEADD(HOUR, -l.hours, SYSUTCDATETIME())
-- ---- questions asked of the log (the baseline is not a spike, and the system's own alerts are not counted)
UNION ALL
SELECT N'violation_spike', N'vault', N'', CAST(0 AS BIT), NULL, NULL
FROM (SELECT COUNT(*) AS opened FROM [{schema}].health_events
      WHERE event_type = N'health.violation_opened' AND is_baseline = 0 AND rule_id NOT LIKE N'E%' AND rule_id NOT LIKE N'Y%'
        AND occurred_at >= DATEADD(HOUR, -(SELECT hours FROM [{schema}].rule_limits WHERE rule_name = N'violation_spike'), SYSUTCDATETIME())) AS s
WHERE s.opened > (SELECT max_count FROM [{schema}].rule_limits WHERE rule_name = N'violation_spike')
UNION ALL
SELECT N'violation_flapping', h.entity_id, h.rule_id + N': ' + (h.detail COLLATE Latin1_General_100_BIN2), CAST(MAX(CAST(h.in_archive AS INT)) AS BIT), NULL, NULL
FROM [{schema}].health_events AS h
WHERE h.event_type = N'health.violation_opened' AND h.rule_id NOT LIKE N'E%' AND h.rule_id NOT LIKE N'Y%'
  AND h.occurred_at >= DATEADD(DAY, -(SELECT days FROM [{schema}].rule_limits WHERE rule_name = N'violation_flapping'), SYSUTCDATETIME())
GROUP BY h.rule_id, h.entity_id, h.detail
HAVING COUNT(*) >= (SELECT max_count FROM [{schema}].rule_limits WHERE rule_name = N'violation_flapping')
UNION ALL
SELECT N'mass_change', N'obsidian', r.run_id, CAST(0 AS BIT), NULL, NULL
FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY finished_at DESC, event_id DESC) AS recent FROM [{schema}].fact_sync_runs WHERE ok = 1 AND system = N'obsidian') AS r
JOIN [{schema}].rule_limits AS l ON l.rule_name = N'mass_change'
WHERE r.recent = 1 AND r.notes_updated > l.max_count
UNION ALL
SELECT N'activity_drought', N'vault', CAST(CAST(a.last_activity AS DATE) AS NVARCHAR(10)), CAST(0 AS BIT), NULL, NULL
FROM (SELECT MAX(inserted_at) AS last_activity FROM [{schema}].note_events WHERE event_type IN (N'note.created', N'note.updated') AND is_baseline = 0) AS a
JOIN [{schema}].rule_limits AS l ON l.rule_name = N'activity_drought'
WHERE a.last_activity < DATEADD(DAY, -l.days, SYSUTCDATETIME());
GO
EXEC [{schema}].episteme_describe_view N'health_detections', N'The violations of the structure, knowledge, cadence, task, system and event rules found right now: rule_name, entity_id, detail, in_archive, note_type and a severity of its own when the detector has one. The limits come from rule_limits.';
GO


CREATE OR ALTER VIEW [{schema}].health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    d.entity_id,
    d.detail,
    d.in_archive,
    d.note_type,
    COALESCE(d.severity, r.severity) AS severity
FROM (
    SELECT [rule] AS rule_name, path COLLATE Latin1_General_100_BIN2 AS entity_id, COALESCE(detail, N'') COLLATE Latin1_General_100_BIN2 AS detail, in_archive, type COLLATE Latin1_General_100_BIN2 AS note_type, CAST(NULL AS NVARCHAR(20)) COLLATE Latin1_General_100_BIN2 AS severity
    FROM [{schema}].property_issues
    UNION ALL
    SELECT rule_name, entity_id, detail, in_archive, note_type, severity FROM [{schema}].health_detections
) AS d
JOIN [{schema}].dim_rule AS r ON r.name = d.rule_name COLLATE Latin1_General_100_BIN2 AND r.valid_to IS NULL;
GO
EXEC [{schema}].episteme_describe_view N'health_findings', N'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it and its severity (the detector''s own, else the rule''s). The input of the health check.';
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
    JSON_VALUE(payload, N'$.severity')                                                      AS severity,
    CAST(CASE WHEN JSON_VALUE(payload, N'$.baseline') = N'true' THEN 1 ELSE 0 END AS BIT)  AS is_baseline,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    payload
FROM [{schema}].events
WHERE category = N'health' AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'health_events', N'The health events (violation_opened, violation_resolved, severity_changed) with detail, in_archive, note_type, reason (why it was resolved or its severity changed), severity and is_baseline as columns.';
GO


CREATE OR ALTER VIEW [{schema}].fact_violations AS
WITH numbered AS (
    SELECT
        *,
        SUM(CASE WHEN event_type = N'health.violation_opened' THEN 1 ELSE 0 END)
            OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id ROWS UNBOUNDED PRECEDING) AS episode_no
    FROM [{schema}].health_events
    WHERE event_type IN (N'health.violation_opened', N'health.violation_resolved', N'health.severity_changed')
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY rule_id, entity_id, detail, episode_no
                           ORDER BY CASE WHEN severity IS NULL THEN 1 ELSE 0 END, occurred_at DESC, event_id DESC) AS latest_severity
    FROM numbered
    WHERE episode_no >= 1
),
episodes AS (
    SELECT
        rule_id, entity_id, detail, episode_no,
        MIN(CASE WHEN event_type = N'health.violation_opened' THEN event_id END)                  AS violation_id,
        MIN(CASE WHEN event_type = N'health.violation_opened' THEN event_version END)             AS event_version,
        MAX(CASE WHEN event_type = N'health.violation_opened' THEN CAST(in_archive AS INT) END)   AS in_archive,
        MIN(CASE WHEN event_type = N'health.violation_opened' THEN note_type END)                 AS note_type,
        MAX(CASE WHEN event_type = N'health.violation_opened' THEN CAST(is_baseline AS INT) END)  AS is_baseline,
        MIN(CASE WHEN event_type = N'health.violation_opened' THEN occurred_at END)               AS opened_at,
        MIN(CASE WHEN event_type = N'health.violation_resolved' THEN occurred_at END)             AS resolved_at,
        MIN(CASE WHEN event_type = N'health.violation_resolved' THEN reason END)                  AS resolution,
        MIN(CASE WHEN latest_severity = 1 THEN severity END)                                      AS severity
    FROM ranked
    GROUP BY rule_id, entity_id, detail, episode_no
)
SELECT
    e.violation_id,
    e.rule_id,
    r.rule_sk,
    COALESCE(e.severity, r.severity)                                         AS severity,
    e.entity_id,
    e.detail,
    CAST(e.in_archive AS BIT)                                                AS in_archive,
    e.note_type,
    CAST(e.is_baseline AS BIT)                                               AS is_baseline,
    e.opened_at,
    CAST(e.opened_at AS DATE)                                                AS opened_date,
    e.resolved_at,
    e.resolution,
    CAST(CASE WHEN e.resolved_at IS NULL THEN 1 ELSE 0 END AS BIT)           AS is_open,
    DATEDIFF(DAY, e.opened_at, COALESCE(e.resolved_at, SYSUTCDATETIME()))    AS days_open,
    e.episode_no - 1                                                         AS reopen_count
FROM episodes AS e
LEFT JOIN [{schema}].dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version
WHERE e.violation_id IS NOT NULL;
GO
EXEC [{schema}].episteme_describe_view N'fact_violations', N'One row per violation episode: opened_at, resolved_at (NULL while open), resolution (fixed or note_gone), severity (the latest of the episode: it can escalate), days_open (until now while open; a lower bound for is_baseline episodes), reopen_count (0 for the first episode of a violation). Keys: rule_sk (dim_rule), severity (dim_severity), opened_date (dim_date), entity_id (dim_note for notes).';
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
EXEC [{schema}].episteme_describe_view N'open_alerts', N'The open violations of the active vault (not the archive), with their current severity, how long they have been open and what the rule says about them. Sort by severity_rank DESC, days_open DESC.';
GO


CREATE OR ALTER VIEW [{schema}].task_events AS
SELECT
    event_id,
    event_type,
    entity_id                                                                       AS task_id,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    CAST(CASE WHEN event_type = N'task.discovered' THEN 1 ELSE 0 END AS BIT)        AS is_baseline,
    JSON_VALUE(payload, N'$.note_path')                                             AS note_path,
    JSON_VALUE(payload, N'$.state')                                                 AS state,
    JSON_VALUE(payload, N'$.previous_state')                                        AS previous_state,
    JSON_VALUE(payload, N'$.due_date')                                              AS due_date,
    JSON_VALUE(payload, N'$.priority')                                              AS priority,
    JSON_QUERY(payload, N'$.changed')                                               AS changed,
    payload
FROM [{schema}].events
WHERE system = N'obsidian' AND event_type LIKE N'task.%' AND is_deleted = 0;
GO
EXEC [{schema}].episteme_describe_view N'task_events', N'The events about tasks (discovered, created, updated, completed, deleted) with note_path, state, previous_state, due_date and priority as columns. Never the text of a task.';
GO


CREATE OR ALTER VIEW [{schema}].activity_daily AS
WITH notes AS (
    SELECT
        occurred_date,
        COUNT(CASE WHEN event_type = N'note.created' THEN 1 END)  AS notes_created,
        COUNT(CASE WHEN event_type = N'note.updated' THEN 1 END)  AS notes_updated,
        COUNT(CASE WHEN event_type = N'note.moved'   THEN 1 END)  AS notes_moved,
        COUNT(CASE WHEN event_type = N'note.deleted' THEN 1 END)  AS notes_deleted,
        COUNT(DISTINCT path)                                      AS notes_touched
    FROM [{schema}].note_events
    WHERE is_baseline = 0
    GROUP BY occurred_date
),
tasks AS (
    SELECT
        occurred_date,
        COUNT(CASE WHEN event_type = N'task.created'   THEN 1 END) AS tasks_created,
        COUNT(CASE WHEN event_type = N'task.completed' THEN 1 END) AS tasks_completed
    FROM [{schema}].task_events
    WHERE is_baseline = 0
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
GO
EXEC [{schema}].episteme_describe_view N'activity_daily', N'Per day: notes created, updated, moved and deleted, how many different notes that touched, and tasks created and completed. Baseline (discovered) events are not counted.';
GO
