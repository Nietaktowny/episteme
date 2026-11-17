-- Stores one event, unless one with the same dedupe_key is already stored. inserted_at is the database's.
INSERT INTO [{schema}].events (
    event_id, system, category, event_type, event_version, rule_id, entity_id,
    occurred_at, run_id, producer, dedupe_key, payload
)
SELECT
    CAST($event_id AS NVARCHAR(36)),
    CAST($system AS NVARCHAR(50)),
    CAST($category AS NVARCHAR(50)),
    CAST($event_type AS NVARCHAR(100)),
    CAST($event_version AS INT),
    CAST($rule_id AS NVARCHAR(50)),
    CAST($entity_id AS NVARCHAR(450)),
    CAST($occurred_at AS DATETIME2(6)),
    CAST($run_id AS NVARCHAR(36)),
    CAST($producer AS NVARCHAR(100)),
    CAST($dedupe_key AS NVARCHAR(450)),
    CAST($payload AS NVARCHAR(MAX))
WHERE NOT EXISTS (SELECT 1 FROM [{schema}].events WHERE dedupe_key = CAST($dedupe_key AS NVARCHAR(450)))
