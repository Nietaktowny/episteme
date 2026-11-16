-- Views that analyze the vault as a whole: Anki state, source topics, property inventory,
-- registry validation and the PKM design hierarchy. They build on the views of V3.

-- ---------------------------------------------------------------------------
-- Evergreen notes against their Anki processing checkpoint.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW evergreen_anki_state AS
SELECT
    path, name, in_archive, domain, ankified_date, modified_at,
    -- 'stale' compares the file's mtime with the checkpoint, so it can over-report after files
    -- were re-synced between machines: treat it as a hint to look, not as proof.
    CASE
        WHEN ankified_date IS NULL                    THEN 'never'
        WHEN modified_at::DATE > ankified_date        THEN 'stale'
        ELSE 'current'
    END AS anki_state
FROM evergreen_notes;
COMMENT ON VIEW evergreen_anki_state IS 'Evergreen notes with anki_state: never (no ankified_date), stale (modified after it, by file mtime) or current.';


-- ---------------------------------------------------------------------------
-- One row per source and topic.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW source_topics AS
SELECT path, name, in_archive, unnest(topics) AS topic
FROM source_notes
WHERE topics IS NOT NULL;
COMMENT ON VIEW source_topics IS 'The topics of every source, one row each: for cross-source retrieval.';


-- ---------------------------------------------------------------------------
-- Which frontmatter properties exist, per type: finds typos and unregistered properties.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW property_usage AS
SELECT
    COALESCE(type, '(no type)') AS type,
    key,
    count(*) AS notes
FROM (SELECT type, unnest(json_keys(frontmatter)) AS key FROM vault_notes)
GROUP BY ALL;
COMMENT ON VIEW property_usage IS 'Every frontmatter property with the number of notes that use it, per note type.';


-- ---------------------------------------------------------------------------
-- The PKM design hierarchy, from the `parent` property.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW pkm_design_tree AS
WITH RECURSIVE
    design AS (
        SELECT path, name, link_target(frontmatter ->> '$.parent') AS parent
        FROM vault_notes
        WHERE type = 'pkm_design'
    ),
    tree(path, name, parent, depth, trail) AS (
        -- Roots: no parent, or a parent that is not a design note (so orphans stay visible).
        SELECT path, name, parent, 0, name
        FROM design
        WHERE parent IS NULL OR parent NOT IN (SELECT name FROM design)
        UNION ALL
        SELECT d.path, d.name, d.parent, t.depth + 1, t.trail || ' > ' || d.name
        FROM design d
        JOIN tree t ON d.parent = t.name
        WHERE t.depth < 20            -- a safety net against accidental cycles
    )
SELECT path, name, parent, depth, trail FROM tree;
COMMENT ON VIEW pkm_design_tree IS 'The pkm_design notes as a tree: parent, depth and trail ("Root > Child > ..."). Notes whose parent is missing show up as roots.';


-- ---------------------------------------------------------------------------
-- Violations of the property registry, as rows. Active vault only: filter NOT in_archive,
-- because the registry does not require rewriting archived notes.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW property_issues AS
WITH
    all_notes AS (SELECT * FROM vault_notes),
    issues AS (
        -- `type` must be one of the registered values.
        SELECT path, 'unknown_type' AS rule, type AS detail
        FROM all_notes
        WHERE type IS NOT NULL
          AND type NOT IN ('pkm_design', 'lane', 'project', 'evergreen', 'source', 'daily_note',
                           'weekly_review', 'weekly_status', 'meeting', 'roadmap', 'words_list')

        -- Structured notes need a `created` date, written YYYY-MM-DD.
        UNION ALL
        SELECT path, 'missing_created', type
        FROM all_notes
        WHERE type IS NOT NULL AND frontmatter ->> '$.created' IS NULL

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
        WHERE kind IS NOT NULL AND kind NOT IN ('article', 'book', 'docs', 'video', 'course')
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
