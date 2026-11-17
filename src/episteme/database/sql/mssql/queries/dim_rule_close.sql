UPDATE [{schema}].dim_rule SET valid_to = CAST($valid_to AS DATETIME2(6)), is_active = 0 WHERE rule_sk = $rule_sk
