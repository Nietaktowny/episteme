-- Views that analyze the vault as a whole: Anki state, source topics, property inventory,
-- registry validation and the PKM design hierarchy. They build on the views of V3.

-- ---------------------------------------------------------------------------
-- Evergreen notes against their Anki processing checkpoint.
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].evergreen_anki_state AS
SELECT
    path, name, in_archive, domain, ankified_date, modified_at,
    -- 'stale' compares the file's mtime with the checkpoint, so it can over-report after files
    -- were re-synced between machines: treat it as a hint to look, not as proof.
    CASE
        WHEN ankified_date IS NULL                    THEN N'never'
        WHEN CAST(modified_at AS DATE) > ankified_date THEN N'stale'
        ELSE N'current'
    END AS anki_state
FROM [{schema}].evergreen_notes;
GO
EXEC [{schema}].episteme_describe_view N'evergreen_anki_state', N'Evergreen notes with anki_state: never (no ankified_date), stale (modified after it, by file mtime) or current.';
GO


-- ---------------------------------------------------------------------------
-- One row per source and topic.
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].source_topics AS
SELECT s.path, s.name, s.in_archive, j.value AS topic
FROM [{schema}].source_notes AS s
CROSS APPLY OPENJSON(s.topics) AS j
WHERE s.topics IS NOT NULL;
GO
EXEC [{schema}].episteme_describe_view N'source_topics', N'The topics of every source, one row each: for cross-source retrieval.';
GO


-- ---------------------------------------------------------------------------
-- Which frontmatter properties exist, per type: finds typos and unregistered properties.
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].property_usage AS
SELECT
    COALESCE(n.type, N'(no type)')                                      AS type,
    k.[key] COLLATE Latin1_General_100_BIN2                             AS [key],
    COUNT(*)                                                            AS notes
FROM [{schema}].vault_notes AS n
CROSS APPLY OPENJSON(n.frontmatter) AS k
GROUP BY COALESCE(n.type, N'(no type)'), k.[key] COLLATE Latin1_General_100_BIN2;
GO
EXEC [{schema}].episteme_describe_view N'property_usage', N'Every frontmatter property with the number of notes that use it, per note type.';
GO


-- ---------------------------------------------------------------------------
-- The PKM design hierarchy, from the `parent` property.
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].pkm_design_tree AS
WITH
    design AS (
        SELECT path, name, [{schema}].link_target([{schema}].json_text(frontmatter, N'$.parent')) AS parent
        FROM [{schema}].vault_notes
        WHERE type = N'pkm_design'
    ),
    tree(path, name, parent, depth, trail) AS (
        -- Roots: no parent, or a parent that is not a design note (so orphans stay visible).
        SELECT path, name, parent, 0, CAST(name AS NVARCHAR(4000))
        FROM design
        WHERE parent IS NULL OR parent NOT IN (SELECT name FROM design)
        UNION ALL
        SELECT d.path, d.name, d.parent, t.depth + 1, CAST(t.trail + N' > ' + d.name AS NVARCHAR(4000))
        FROM design AS d
        JOIN tree AS t ON d.parent = t.name
        WHERE t.depth < 20            -- a safety net against accidental cycles
    )
SELECT path, name, parent, depth, trail FROM tree;
GO
EXEC [{schema}].episteme_describe_view N'pkm_design_tree', N'The pkm_design notes as a tree: parent, depth and trail ("Root > Child > ..."). Notes whose parent is missing show up as roots.';
GO


-- ---------------------------------------------------------------------------
-- Violations of the property registry, as rows. Active vault only: filter in_archive = 0,
-- because the registry does not require rewriting archived notes.
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].property_issues AS
WITH
    all_notes AS (SELECT * FROM [{schema}].vault_notes),
    issues AS (
        -- `type` must be one of the registered values.
        SELECT path, N'unknown_type' AS [rule], CAST(type AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2 AS detail
        FROM all_notes
        WHERE type IS NOT NULL
          AND type NOT IN (N'pkm_design', N'lane', N'project', N'evergreen', N'source', N'daily_note',
                           N'weekly_review', N'weekly_status', N'meeting', N'roadmap', N'words_list')

        -- Structured notes need a `created` date, written YYYY-MM-DD.
        UNION ALL
        SELECT path, N'missing_created', CAST(type AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE type IS NOT NULL AND [{schema}].json_text(frontmatter, N'$.created') IS NULL

        -- Controlled `status` values per type.
        UNION ALL
        SELECT path, N'invalid_status', CAST(type + N': ' + status AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE status IS NOT NULL AND (
              (type = N'project'    AND status NOT IN (N'active', N'paused', N'done', N'abandoned'))
           OR (type = N'meeting'    AND status NOT IN (N'inbox', N'processed'))
           OR (type = N'pkm_design' AND status NOT IN (N'draft', N'active', N'frozen', N'deprecated')))

        -- `closed_date` is set exactly when a project is done or abandoned.
        UNION ALL
        SELECT path, N'terminal_without_closed_date', CAST(status AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].project_notes
        WHERE status IN (N'done', N'abandoned') AND closed_date IS NULL
        UNION ALL
        SELECT path, N'closed_date_on_open_project', CAST(status AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].project_notes
        WHERE status IN (N'active', N'paused') AND [{schema}].json_text(frontmatter, N'$.closed_date') IS NOT NULL

        -- Date properties must be dates (YYYY-MM-DD). A null `ankified_date` is valid.
        UNION ALL
        SELECT n.path, N'invalid_date', CAST(p.property AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes AS n
        CROSS JOIN (VALUES (N'created'), (N'closed_date'), (N'last_review'), (N'consumed_on'),
                           (N'start_range'), (N'end_range'), (N'ankified_date')) AS p(property)
        WHERE [{schema}].json_text(n.frontmatter, N'$.' + p.property) IS NOT NULL
          AND TRY_CAST([{schema}].json_text(n.frontmatter, N'$.' + p.property) AS DATE) IS NULL

        -- Deprecated properties.
        UNION ALL
        SELECT n.path, N'deprecated_property', CAST(k.[key] AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes AS n
        CROSS APPLY OPENJSON(n.frontmatter) AS k
        WHERE k.[key] COLLATE Latin1_General_100_BIN2 IN (N'updated', N'lane_tag', N'related', N'area', N'ankified')

        -- Relationships are links, and the linked note must exist.
        UNION ALL
        SELECT path, N'lane_not_a_link', CAST([{schema}].json_text(frontmatter, N'$.lane') AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE [{schema}].json_text(frontmatter, N'$.lane') IS NOT NULL AND [{schema}].link_target([{schema}].json_text(frontmatter, N'$.lane')) IS NULL
        UNION ALL
        SELECT path, N'unresolved_lane', CAST([{schema}].link_target([{schema}].json_text(frontmatter, N'$.lane')) AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE [{schema}].link_target([{schema}].json_text(frontmatter, N'$.lane')) IS NOT NULL
          AND [{schema}].link_target([{schema}].json_text(frontmatter, N'$.lane')) NOT IN (SELECT name FROM all_notes WHERE type = N'lane')
        UNION ALL
        SELECT path, N'parent_not_a_link', CAST([{schema}].json_text(frontmatter, N'$.parent') AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE [{schema}].json_text(frontmatter, N'$.parent') IS NOT NULL AND [{schema}].link_target([{schema}].json_text(frontmatter, N'$.parent')) IS NULL
        UNION ALL
        SELECT path, N'unresolved_parent', CAST([{schema}].link_target([{schema}].json_text(frontmatter, N'$.parent')) AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM all_notes
        WHERE [{schema}].link_target([{schema}].json_text(frontmatter, N'$.parent')) IS NOT NULL
          AND [{schema}].link_target([{schema}].json_text(frontmatter, N'$.parent')) NOT IN (SELECT name FROM all_notes)

        -- Sources: a registered `kind`, an integer `year`, `topics` as a list.
        UNION ALL
        SELECT path, N'missing_kind', CAST(NULL AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2 FROM [{schema}].source_notes WHERE kind IS NULL
        UNION ALL
        SELECT path, N'invalid_kind', CAST(kind AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].source_notes
        WHERE kind IS NOT NULL AND kind NOT IN (N'article', N'book', N'docs', N'video', N'course')
        UNION ALL
        SELECT path, N'invalid_year', CAST([{schema}].json_text(frontmatter, N'$.year') AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].source_notes
        WHERE [{schema}].json_text(frontmatter, N'$.year') IS NOT NULL AND [year] IS NULL
        UNION ALL
        SELECT s.path, N'topics_not_a_list', CAST(k.value AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].source_notes AS s
        CROSS APPLY OPENJSON(s.frontmatter) AS k
        WHERE k.[key] COLLATE Latin1_General_100_BIN2 = N'topics' AND k.type NOT IN (0, 4)

        -- Review cycles and review ranges.
        UNION ALL
        SELECT path, N'invalid_review_cycle', CAST(review_cycle AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].review_schedule
        WHERE review_cycle NOT IN (N'weekly', N'monthly')
        UNION ALL
        SELECT path, N'missing_range', CAST(NULL AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].weekly_reviews
        WHERE start_range IS NULL OR end_range IS NULL
        UNION ALL
        SELECT path, N'inverted_range', CAST(CONVERT(NVARCHAR(30), start_range, 23) + N' > ' + CONVERT(NVARCHAR(30), end_range, 23) AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].weekly_reviews
        WHERE start_range > end_range

        -- Controlled text values are lowercase_snake_case: [a-z0-9] groups joined by single underscores.
        UNION ALL
        SELECT path, N'invalid_domain_format', CAST(domain AS NVARCHAR(4000)) COLLATE Latin1_General_100_BIN2
        FROM [{schema}].evergreen_notes
        WHERE domain IS NOT NULL
          AND (domain = N'' OR domain LIKE N'%[^a-z0-9_]%'
               OR LEFT(domain, 1) = N'_' OR RIGHT(domain, 1) = N'_' OR CHARINDEX(N'__', domain) > 0)
    )
SELECT n.path, n.name, n.type, n.in_archive, i.[rule], i.detail
FROM issues AS i
JOIN all_notes AS n ON n.path = i.path;
GO
EXEC [{schema}].episteme_describe_view N'property_issues', N'Violations of the property registry, one row per issue (rule, detail). Filter in_archive = 0: archived notes are not rewritten.';
