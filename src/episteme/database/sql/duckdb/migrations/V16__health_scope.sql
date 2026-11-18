-- What the registry and structure rules do not look at.
--
-- Some of what the rules found was noise: the rules were checking notes that are not meant to follow the registry. health_findings
-- (the input of the health check, see V14) now leaves these out, so that they are never opened as alerts:
--
--   templates     (type 'template', or in 99_Templates/)  are not checked by R2 missing_created, R6 invalid_date, R7 deprecated_property,
--                 S2 duplicate_content and S5 unexpected_empty_note: they hold {{placeholders}}, and consecutive versions are near copies.
--   _system/      the notes that tools write are not checked by R1 unknown_type, R2 missing_created and S3 untyped_note.
--   00_Inbox/     unstructured capture needs no type (Properties.md), so S3 untyped_note does not look at it.
--   legacy notes  R2 missing_created and R17 missing_range only apply to notes made from a template that required them: a note whose
--                 template_version is missing or below 1.1 predates `created` (sources) and `start_range`/`end_range` (weekly reviews),
--                 and Properties.md does not rewrite history.

CREATE OR REPLACE VIEW health_findings AS
SELECT
    r.rule_id,
    r.rule_version,
    d.entity_id,
    d.detail,
    d.in_archive,
    d.note_type,
    COALESCE(d.severity, r.severity) AS severity
FROM (
    SELECT rule AS rule_name, path AS entity_id, COALESCE(detail, '') AS detail, in_archive, type AS note_type, CAST(NULL AS VARCHAR) AS severity
    FROM property_issues
    UNION ALL
    SELECT rule_name, entity_id, detail, in_archive, note_type, severity FROM health_detections
) AS d
JOIN dim_rule AS r ON r.name = d.rule_name AND r.valid_to IS NULL
LEFT JOIN notes AS n ON n.path = d.entity_id
WHERE NOT (
       (d.rule_name IN ('missing_created', 'invalid_date', 'deprecated_property', 'duplicate_content', 'unexpected_empty_note')
            AND (COALESCE(d.note_type, '') = 'template' OR left(d.entity_id, 13) = '99_Templates/'))
    OR (d.rule_name IN ('unknown_type', 'missing_created', 'untyped_note') AND left(d.entity_id, 8) = '_system/')
    OR (d.rule_name = 'untyped_note' AND left(d.entity_id, 9) = '00_Inbox/')
    OR (d.rule_name IN ('missing_created', 'missing_range') AND COALESCE(TRY_CAST(n.frontmatter ->> '$.template_version' AS DOUBLE), 0) < 1.1)
);
COMMENT ON VIEW health_findings IS 'Every violation found right now, one row per (rule_id, entity_id, detail), with the version of the rule that found it and its severity (the detector''s own, else the rule''s). The input of the health check. It leaves out what the rules are not meant to check: templates, _system/, the inbox (S3) and notes older than the template version that required the property (R2, R17).';
