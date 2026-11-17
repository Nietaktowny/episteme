-- The open alerts a sink has not received at their current severity. $limit rows at most, most severe first.
SELECT TOP (CAST($limit AS INT)) a.violation_id, a.rule_id, a.rule_name, a.severity, a.severity_rank, a.entity_id, a.note_name, a.note_type, a.detail,
       a.opened_at, a.days_open, a.reopen_count, a.is_baseline, a.description, a.remediation
FROM [{schema}].open_alerts AS a
WHERE a.severity_rank >= CAST($min_rank AS INT)
  AND (CAST($include_baseline AS BIT) = 1 OR a.is_baseline = 0)
  AND NOT EXISTS (
      SELECT 1 FROM [{schema}].events AS e
      WHERE e.event_type = N'alert.delivered' AND e.is_deleted = 0 AND e.entity_id = a.violation_id
        AND JSON_VALUE(e.payload, N'$.sink') = $sink AND JSON_VALUE(e.payload, N'$.severity') = a.severity COLLATE Latin1_General_100_BIN2)
ORDER BY a.severity_rank DESC, a.opened_at, a.violation_id
