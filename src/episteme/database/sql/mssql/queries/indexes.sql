SELECT i.name
FROM sys.indexes AS i
JOIN sys.tables AS t ON t.object_id = i.object_id
WHERE t.schema_id = SCHEMA_ID(N'{schema}') AND i.is_primary_key = 0 AND i.is_unique_constraint = 0 AND i.name IS NOT NULL
