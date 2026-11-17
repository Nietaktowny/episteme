INSERT INTO dim_rule (
    rule_sk, rule_id, rule_version, logic_version, params_hash, name, rule_group, kind, applies_to,
    severity, description, remediation, parameters, is_active, valid_from
) VALUES (
    $rule_sk, $rule_id, $rule_version, $logic_version, $params_hash, $name, $rule_group, $kind, $applies_to,
    $severity, $description, $remediation, $parameters, $is_active, $valid_from
)
