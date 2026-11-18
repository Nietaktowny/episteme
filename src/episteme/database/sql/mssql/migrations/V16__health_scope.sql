-- What the registry and structure rules do not look at. The same view, with the same name and columns, as in the DuckDB schema
-- (see sql/duckdb/migrations/V16, which explains it), written in T-SQL.

CREATE OR ALTER VIEW [{schema}].health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    d.entity_id,
    d.detail,
    d.in_archive,
    d.note_type,
    COALESCE(d.severity, r.severity) AS severity
FROM (
    SELECT [rule] AS rule_name, path COLLATE Latin1_General_100_BIN2 AS entity_id, COALESCE(detail, N'') COLLATE Latin1_General_100_BIN2 AS detail, in_archive, type COLLATE Latin1_General_100_BIN2 AS note_type, CAST(NULL AS NVARCHAR(20)) COLLATE Latin1_General_100_BIN2 AS severity
    FROM [{schema}].property_issues
    UNION ALL
    SELECT rule_name, entity_id, detail, in_archive, note_type, severity FROM [{schema}].health_detections
) AS d
JOIN [{schema}].dim_rule AS r ON r.name = d.rule_name COLLATE Latin1_General_100_BIN2 AND r.valid_to IS NULL
LEFT JOIN [{schema}].notes AS n ON n.path COLLATE Latin1_General_100_BIN2 = d.entity_id
WHERE NOT (
       (d.rule_name IN (N'missing_created', N'invalid_date', N'deprecated_property', N'duplicate_content', N'unexpected_empty_note')
            AND (COALESCE(d.note_type, N'') = N'template' OR LEFT(d.entity_id, 13) = N'99_Templates/'))
    OR (d.rule_name IN (N'unknown_type', N'missing_created', N'untyped_note') AND LEFT(d.entity_id, 8) = N'_system/')
    OR (d.rule_name = N'untyped_note' AND LEFT(d.entity_id, 9) = N'00_Inbox/')
    OR (d.rule_name IN (N'missing_created', N'missing_range') AND COALESCE(TRY_CAST(JSON_VALUE(n.frontmatter, N'$.template_version') AS FLOAT), 0) < 1.1)
);
GO
EXEC [{schema}].episteme_describe_view N'health_findings', N'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it and its severity (the detector''s own, else the rule''s). The input of the health check. It leaves out what the rules are not meant to check: templates, _system/, the inbox (S3) and notes older than the template version that required the property (R2, R17).';
GO
