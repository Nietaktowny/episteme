-- The health lifecycle: detectors say what is wrong now, the event log remembers since when.
--
--   health_findings  detector  every violation found right now, one row each: (rule, entity, detail). The contract of a
--                              detector: any view that returns these columns can be added to the UNION below. The
--                              registry rules R1-R19 are evaluated by property_issues; the others will follow.
--   health_events    silver    the health events, with the payload's fields as columns
--   fact_violations  gold      one row per violation *episode*: opened, then resolved (or still open). A violation that
--                              comes back is a new episode with reopen_count + 1.
--   open_alerts      gold      the episodes that are open, in the active vault, with what the rule says about them
--
-- A violation is identified by (rule_id, entity_id, detail): a note with two deprecated properties has two.
-- Episteme compares health_findings with the open episodes after every sync and records the difference
-- (Database.check_health). The first check of a database finds everything at once, and those episodes are marked
-- is_baseline: their opened_at is when they were first seen, not when they began, so days_open is a lower bound.

CREATE OR REPLACE VIEW health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    p.path                  AS entity_id,
    COALESCE(p.detail, '')  AS detail,
    p.in_archive,
    p.type                  AS note_type
FROM property_issues AS p
JOIN dim_rule AS r ON r.name = p.rule AND r.rule_group = 'registry' AND r.valid_to IS NULL;
COMMENT ON VIEW health_findings IS 'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it. The input of the health check; the registry rules (R1-R19) for now.';


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
    COALESCE((payload ->> '$.baseline') = 'true', FALSE)    AS is_baseline,
    occurred_at,
    occurred_date,
    inserted_at,
    run_id,
    payload
FROM events
WHERE category = 'health' AND NOT is_deleted;
COMMENT ON VIEW health_events IS 'The health events (violation_opened, violation_resolved) with detail, in_archive, note_type, reason (why it was resolved: fixed or note_gone) and is_baseline as columns.';


CREATE OR REPLACE VIEW fact_violations AS
WITH sequence AS (
    SELECT
        *,
        LEAD(event_type) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id)  AS next_type,
        LEAD(occurred_at) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id) AS next_at,
        LEAD(reason) OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id)      AS next_reason,
        count(*) FILTER (WHERE event_type = 'health.violation_opened')
            OVER (PARTITION BY rule_id, entity_id, detail ORDER BY occurred_at, event_id) - 1           AS reopen_count
    FROM health_events
),
episodes AS (
    SELECT
        event_id, rule_id, event_version, entity_id, detail, in_archive, note_type, is_baseline, reopen_count,
        occurred_at AS opened_at,
        occurred_date AS opened_date,
        CASE WHEN next_type = 'health.violation_resolved' THEN next_at END     AS resolved_at,
        CASE WHEN next_type = 'health.violation_resolved' THEN next_reason END AS resolution
    FROM sequence
    WHERE event_type = 'health.violation_opened'
)
SELECT
    e.event_id                                                                  AS violation_id,
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
    e.resolved_at IS NULL                                                       AS is_open,
    date_diff('day', e.opened_at, COALESCE(e.resolved_at, now() AT TIME ZONE 'UTC')) AS days_open,
    e.reopen_count
FROM episodes AS e
LEFT JOIN dim_rule AS r ON r.rule_id = e.rule_id AND r.rule_version = e.event_version;
COMMENT ON VIEW fact_violations IS 'One row per violation episode: opened_at, resolved_at (NULL while open), resolution (fixed or note_gone), days_open (until now while open; a lower bound for is_baseline episodes), reopen_count (0 for the first episode of a violation). Keys: rule_sk (dim_rule), severity (dim_severity), opened_date (dim_date), entity_id (dim_note).';


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
COMMENT ON VIEW open_alerts IS 'The open violations of the active vault (not the archive), with severity, how long they have been open and what the rule says about them. Sort by severity_rank DESC, days_open DESC.';
