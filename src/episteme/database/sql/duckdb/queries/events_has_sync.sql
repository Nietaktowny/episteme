SELECT 1 AS found FROM events WHERE event_type = 'sync.completed' AND system = $system AND NOT is_deleted LIMIT 1
