-- `chat` is a registered kind of source: a conversation with an AI assistant kept as a source (Properties.md).
-- property_issues is the V10 view with 'chat' added to the kinds that R13 accepts.

CREATE OR REPLACE VIEW property_issues AS
WITH
    all_notes AS (SELECT * FROM vault_notes),
    issues AS (
        -- `type` must be one of the registered values.
        SELECT path, 'unknown_type' AS rule, type AS detail
        FROM all_notes
        WHERE type IS NOT NULL
          AND type NOT IN ('pkm_design', 'lane', 'project', 'evergreen', 'source', 'daily_note',
                           'weekly_review', 'weekly_status', 'meeting', 'roadmap', 'words_list', 'template')

        -- Structured notes need a `created` date, written YYYY-MM-DD. A zero-byte note has a type (its folder's) but
        -- no frontmatter to hold a date: it is a placeholder, not a note that forgot it.
        UNION ALL
        SELECT path, 'missing_created', type
        FROM all_notes
        WHERE type IS NOT NULL AND NOT is_empty AND frontmatter ->> '$.created' IS NULL

        -- Controlled `status` values per type.
        UNION ALL
        SELECT path, 'invalid_status', type || ': ' || status
        FROM all_notes
        WHERE status IS NOT NULL AND (
              (type = 'project'    AND status NOT IN ('active', 'paused', 'done', 'abandoned'))
           OR (type = 'meeting'    AND status NOT IN ('inbox', 'processed'))
           OR (type = 'pkm_design' AND status NOT IN ('draft', 'active', 'frozen', 'deprecated')))

        -- `closed_date` is set exactly when a project is done or abandoned.
        UNION ALL
        SELECT path, 'terminal_without_closed_date', status
        FROM project_notes
        WHERE status IN ('done', 'abandoned') AND closed_date IS NULL
        UNION ALL
        SELECT path, 'closed_date_on_open_project', status
        FROM project_notes
        WHERE status IN ('active', 'paused') AND frontmatter ->> '$.closed_date' IS NOT NULL

        -- Date properties must be dates (YYYY-MM-DD). A null `ankified_date` is valid.
        UNION ALL
        SELECT n.path, 'invalid_date', p.property
        FROM all_notes n
        CROSS JOIN (SELECT unnest(['created', 'closed_date', 'last_review', 'consumed_on',
                                   'start_range', 'end_range', 'ankified_date']) AS property) p
        WHERE json_extract_string(n.frontmatter, '$.' || p.property) IS NOT NULL
          AND TRY_CAST(json_extract_string(n.frontmatter, '$.' || p.property) AS DATE) IS NULL

        -- Deprecated properties.
        UNION ALL
        SELECT path, 'deprecated_property', key
        FROM (SELECT path, unnest(json_keys(frontmatter)) AS key FROM all_notes)
        WHERE key IN ('updated', 'lane_tag', 'related', 'area', 'ankified')

        -- Relationships are links, and the linked note must exist.
        UNION ALL
        SELECT path, 'lane_not_a_link', frontmatter ->> '$.lane'
        FROM all_notes
        WHERE frontmatter ->> '$.lane' IS NOT NULL AND link_target(frontmatter ->> '$.lane') IS NULL
        UNION ALL
        SELECT path, 'unresolved_lane', link_target(frontmatter ->> '$.lane')
        FROM all_notes
        WHERE link_target(frontmatter ->> '$.lane') IS NOT NULL
          AND link_target(frontmatter ->> '$.lane') NOT IN (SELECT name FROM all_notes WHERE type = 'lane')
        UNION ALL
        SELECT path, 'parent_not_a_link', frontmatter ->> '$.parent'
        FROM all_notes
        WHERE frontmatter ->> '$.parent' IS NOT NULL AND link_target(frontmatter ->> '$.parent') IS NULL
        UNION ALL
        SELECT path, 'unresolved_parent', link_target(frontmatter ->> '$.parent')
        FROM all_notes
        WHERE link_target(frontmatter ->> '$.parent') IS NOT NULL
          AND link_target(frontmatter ->> '$.parent') NOT IN (SELECT name FROM all_notes)

        -- Sources: a registered `kind`, an integer `year`, `topics` as a list.
        UNION ALL
        SELECT path, 'missing_kind', NULL FROM source_notes WHERE kind IS NULL
        UNION ALL
        SELECT path, 'invalid_kind', kind
        FROM source_notes
        WHERE kind IS NOT NULL AND kind NOT IN ('article', 'book', 'docs', 'video', 'course', 'chat')
        UNION ALL
        SELECT path, 'invalid_year', frontmatter ->> '$.year'
        FROM source_notes
        WHERE frontmatter ->> '$.year' IS NOT NULL AND year IS NULL
        UNION ALL
        SELECT path, 'topics_not_a_list', frontmatter ->> '$.topics'
        FROM source_notes
        WHERE json_type(frontmatter -> '$.topics') NOT IN ('ARRAY', 'NULL')

        -- Review cycles and review ranges.
        UNION ALL
        SELECT path, 'invalid_review_cycle', review_cycle
        FROM review_schedule
        WHERE review_cycle NOT IN ('weekly', 'monthly')
        UNION ALL
        SELECT path, 'missing_range', NULL
        FROM weekly_reviews
        WHERE start_range IS NULL OR end_range IS NULL
        UNION ALL
        SELECT path, 'inverted_range', CAST(start_range AS VARCHAR) || ' > ' || CAST(end_range AS VARCHAR)
        FROM weekly_reviews
        WHERE start_range > end_range

        -- Controlled text values are lowercase_snake_case.
        UNION ALL
        SELECT path, 'invalid_domain_format', domain
        FROM evergreen_notes
        WHERE domain IS NOT NULL AND NOT regexp_matches(domain, '^[a-z0-9]+(_[a-z0-9]+)*$')
    )
SELECT n.path, n.name, n.type, n.in_archive, i.rule, i.detail
FROM issues i
JOIN all_notes n USING (path);
COMMENT ON VIEW property_issues IS 'Violations of the property registry, one row per issue (rule, detail). Filter NOT in_archive: archived notes are not rewritten.';


COMMENT ON VIEW fact_violations IS 'One row per violation episode: opened_at, resolved_at (NULL while open), resolution (fixed, note_gone, or rule_removed when its rule left the catalog), severity (the latest of the episode: it can escalate), days_open (until now while open; a lower bound for is_baseline episodes), reopen_count (0 for the first episode of a violation). Keys: rule_sk (dim_rule), severity (dim_severity), opened_date (dim_date), entity_id (dim_note for notes).';
