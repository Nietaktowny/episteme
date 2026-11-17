-- `type` and `created` become columns of notes, so a query can filter and index on them instead of decoding the frontmatter.
--
--   * type is what the note writes in its frontmatter; when it writes none, the type its folder implies
--     (OBSIDIAN__TYPE_BY_FOLDER), so that even a zero-byte Evergreen placeholder is an evergreen note. NULL if neither.
--   * created is the day written in the frontmatter's `created`; NULL when there is none (or it is not a date).
--     It is never taken from the file's times: that is created_at, which says something else.
--
-- Stored notes get what their frontmatter says now; the folder-implied types arrive with the next sync.
ALTER TABLE notes ADD COLUMN type TEXT;
ALTER TABLE notes ADD COLUMN created DATE;

-- The index comes before the update: DuckDB cannot create an index in a transaction that has updated the table.
CREATE INDEX idx_notes_type ON notes (type);

UPDATE notes SET
    type = CASE WHEN json_type(frontmatter, '$.type') = 'VARCHAR' THEN NULLIF(trim(frontmatter ->> '$.type'), '') END,
    created = TRY_CAST(frontmatter ->> '$.created' AS DATE);


-- vault_notes reads the columns. It has the same columns as before, so the views built on it keep working.
-- CREATE OR REPLACE drops a view's comment, so it is set again.
CREATE OR REPLACE VIEW vault_notes AS
SELECT
    path,
    name,
    COALESCE(NULLIF(regexp_extract(path, '^(.*)/[^/]+$', 1), ''), '.') AS folder,
    category_directory,
    starts_with(path, '40_Archive/')                                   AS in_archive,
    size,
    size = 0                                                           AS is_empty,
    type,
    created,
    frontmatter ->> '$.status'                                         AS status,
    updated_at                                                         AS modified_at,
    body_hash,
    frontmatter
FROM notes;
COMMENT ON VIEW vault_notes IS 'Every note with type (written in the note, else implied by its folder), created (date), status, folder, in_archive, is_empty, modified_at and body_hash decoded; the base of the other views.';


-- A placeholder has a type now, so these two views say what they said before about the placeholders.
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
FROM evergreen_notes
WHERE NOT is_empty;     -- the zero-byte placeholders are evergreen notes too, but there is nothing in them to process
COMMENT ON VIEW evergreen_anki_state IS 'Evergreen notes (not the empty placeholders) with anki_state: never (no ankified_date), stale (modified after it, by file mtime) or current.';

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
