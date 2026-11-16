MERGE [{schema}].anki_collection WITH (HOLDLOCK) AS target
USING (SELECT
    CAST(1 AS INT)                         AS id,
    CAST($schema_version AS INT)           AS schema_version,
    CAST($scheduler_version AS INT)        AS scheduler_version,
    CAST($created_date AS DATE)            AS created_date,
    CAST($rollover_hour AS INT)            AS rollover_hour,
    CAST($fsrs_enabled AS BIT)             AS fsrs_enabled,
    CAST($modified_at AS DATETIME2(6))     AS modified_at,
    CAST($synced_at AS DATETIME2(6))       AS synced_at
) AS source ON target.id = source.id
WHEN MATCHED THEN UPDATE SET
    schema_version = source.schema_version, scheduler_version = source.scheduler_version,
    created_date = source.created_date, rollover_hour = source.rollover_hour,
    fsrs_enabled = source.fsrs_enabled, modified_at = source.modified_at, synced_at = source.synced_at
WHEN NOT MATCHED THEN INSERT (id, schema_version, scheduler_version, created_date, rollover_hour, fsrs_enabled, modified_at, synced_at)
VALUES (source.id, source.schema_version, source.scheduler_version, source.created_date, source.rollover_hour,
        source.fsrs_enabled, source.modified_at, source.synced_at);
