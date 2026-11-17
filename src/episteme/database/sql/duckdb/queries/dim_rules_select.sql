-- Every version of every rule, current or closed.
SELECT rule_sk, rule_id, rule_version, logic_version, params_hash, name, description, remediation, is_active, valid_from, valid_to
FROM dim_rule
ORDER BY rule_id, rule_version
