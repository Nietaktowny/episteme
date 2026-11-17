-- Every open episode, archive included: the check must resolve those too.
SELECT violation_id, rule_id, entity_id, detail, opened_at, severity FROM [{schema}].fact_violations WHERE is_open = 1
