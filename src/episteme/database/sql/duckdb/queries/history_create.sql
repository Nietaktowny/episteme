CREATE TABLE IF NOT EXISTS schema_history (
    installed_rank    INTEGER PRIMARY KEY,
    version           TEXT NOT NULL UNIQUE,
    description       TEXT NOT NULL,
    script            TEXT NOT NULL,
    checksum          TEXT NOT NULL,
    installed_on      TIMESTAMP NOT NULL DEFAULT current_timestamp,
    execution_time_ms INTEGER NOT NULL
)
