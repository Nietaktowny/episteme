SELECT TOP 1 1 AS found FROM [{schema}].events WHERE event_type = N'health.check_completed' AND is_deleted = 0
