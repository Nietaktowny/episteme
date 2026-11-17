-- Every parameter may be NULL to leave that filter out. Soft-deleted events are left out unless $include_deleted.
SELECT
    event_id, system, category, event_type, event_version, rule_id, entity_id, occurred_at, inserted_at,
    occurred_date, run_id, producer, dedupe_key, deleted_at, is_deleted, payload::VARCHAR AS payload
FROM events
WHERE ($event_type IS NULL OR event_type = $event_type)
  AND ($entity_id IS NULL OR entity_id = $entity_id)
  AND ($system IS NULL OR system = $system)
  AND ($category IS NULL OR category = $category)
  AND ($run_id IS NULL OR run_id = $run_id)
  AND ($include_deleted OR NOT is_deleted)
ORDER BY occurred_at, event_id
