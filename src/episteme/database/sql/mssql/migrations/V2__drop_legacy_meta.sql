-- The schema version is now tracked by schema_history; remove the old table.
DROP TABLE IF EXISTS [{schema}].meta;
