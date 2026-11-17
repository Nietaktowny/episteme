-- Stores one event, unless one with the same dedupe_key is already stored. inserted_at is the database's.
INSERT INTO events (
    event_id, system, category, event_type, event_version, rule_id, entity_id,
    occurred_at, run_id, producer, dedupe_key, payload
) VALUES (
    $event_id, $system, $category, $event_type, $event_version, $rule_id, $entity_id,
    $occurred_at, $run_id, $producer, $dedupe_key, $payload
)
ON CONFLICT (dedupe_key) DO NOTHING
