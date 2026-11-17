-- $min_rank and $rule_id may be NULL to leave that filter out.
SELECT violation_id, rule_id, rule_name, severity, severity_rank, entity_id, note_name, note_type, detail,
       opened_at, days_open, reopen_count, is_baseline, description, remediation
FROM [{schema}].open_alerts
WHERE (CAST($min_rank AS INTEGER) IS NULL OR severity_rank >= CAST($min_rank AS INTEGER))
  AND ($rule_id IS NULL OR rule_id = $rule_id)
ORDER BY severity_rank DESC, days_open DESC, entity_id
