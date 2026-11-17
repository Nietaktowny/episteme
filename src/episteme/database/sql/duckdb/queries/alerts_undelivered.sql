-- The open alerts a sink has not received at their current severity. $limit rows at most, most severe first.
SELECT a.violation_id, a.rule_id, a.rule_name, a.severity, a.severity_rank, a.entity_id, a.note_name, a.note_type, a.detail,
       a.opened_at, a.days_open, a.reopen_count, a.is_baseline, a.description, a.remediation
FROM open_alerts AS a
WHERE a.severity_rank >= $min_rank
  AND ($include_baseline OR NOT a.is_baseline)
  AND NOT EXISTS (
      SELECT 1 FROM events AS e
      WHERE e.event_type = 'alert.delivered' AND NOT e.is_deleted AND e.entity_id = a.violation_id
        AND e.payload ->> '$.sink' = $sink AND e.payload ->> '$.severity' = a.severity)
ORDER BY a.severity_rank DESC, a.opened_at, a.violation_id
LIMIT CAST($limit AS BIGINT)
