SELECT TOP 1 1 AS found FROM [{schema}].events WHERE event_type = N'sync.completed' AND system = $system AND is_deleted = 0
