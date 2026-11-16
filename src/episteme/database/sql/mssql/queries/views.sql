SELECT v.name, CAST(p.value AS NVARCHAR(MAX)) AS comment
FROM sys.views AS v
LEFT JOIN sys.extended_properties AS p
       ON p.class = 1 AND p.major_id = v.object_id AND p.minor_id = 0 AND p.name = N'MS_Description'
WHERE v.schema_id = SCHEMA_ID(N'{schema}') AND v.is_ms_shipped = 0
