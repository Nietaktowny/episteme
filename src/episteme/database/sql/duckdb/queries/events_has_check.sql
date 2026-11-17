SELECT 1 AS found FROM events WHERE event_type = 'health.check_completed' AND NOT is_deleted LIMIT 1
