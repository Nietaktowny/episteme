INSERT INTO anki_collection (
    id, schema_version, scheduler_version, created_date, rollover_hour, fsrs_enabled, modified_at, synced_at
) VALUES (
    1, $schema_version, $scheduler_version, $created_date, $rollover_hour, $fsrs_enabled, $modified_at, $synced_at
)
ON CONFLICT (id) DO UPDATE SET
    schema_version = excluded.schema_version,
    scheduler_version = excluded.scheduler_version,
    created_date = excluded.created_date,
    rollover_hour = excluded.rollover_hour,
    fsrs_enabled = excluded.fsrs_enabled,
    modified_at = excluded.modified_at,
    synced_at = excluded.synced_at
