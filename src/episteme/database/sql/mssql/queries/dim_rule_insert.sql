INSERT INTO [{schema}].dim_rule (
    rule_sk, rule_id, rule_version, logic_version, params_hash, name, rule_group, kind, applies_to,
    severity, description, remediation, parameters, is_active, valid_from
) VALUES (
    $rule_sk, $rule_id, $rule_version, $logic_version, $params_hash, $name, $rule_group, $kind, $applies_to,
    $severity, $description, $remediation, $parameters, CAST($is_active AS BIT), CAST($valid_from AS DATETIME2(6))
)
