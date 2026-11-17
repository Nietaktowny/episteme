UPDATE [{schema}].dim_rule SET is_active = CAST($is_active AS BIT), name = $name, description = $description, remediation = $remediation WHERE rule_sk = $rule_sk
