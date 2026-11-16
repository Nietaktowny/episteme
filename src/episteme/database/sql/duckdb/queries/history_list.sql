SELECT installed_rank, version, description, script, checksum, installed_on, execution_time_ms
FROM schema_history
ORDER BY installed_rank
