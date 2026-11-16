-- Views over the notes table: the vault's conventions, decoded once.
--
-- The conventions are those of the PKM's property registry (Properties.md):
--   * `type` says what a note is, independently of where it lives, so the typed
--     views below filter on `type`, not on folders;
--   * `created` is the semantic creation date, `modified_at` is the file's mtime;
--   * relationships are quoted Obsidian links ("[[Note]]"), decoded by link_target().
-- Only three things depend on folders: the archive (`40_Archive/`), the zero-byte
-- Evergreen placeholders (`30_Resources/Evergreen/`, they carry no frontmatter and so no
-- `type`), and the folder column of vault_notes.
--
-- Views are bound when they are created. A migration that changes the `notes` table
-- must recreate these views (and COMMENT them again: CREATE OR REPLACE drops comments).
--
-- Every view keeps `path` (the key) and `name`; the typed views add the properties
-- of their type on top of the vault_notes columns.

-- link_target('[[Note|alias]]') = 'Note'; also handles '[[Folder/Note#Heading]]'.
-- NULL for anything that is not an Obsidian link.
CREATE OR REPLACE MACRO link_target(link) AS
    NULLIF(trim(regexp_extract(link, '^\[\[([^|#\]]+)', 1)), '');


-- ---------------------------------------------------------------------------
-- vault_notes: every note, with the universal properties decoded.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW vault_notes AS
SELECT
    path,
    name,
    COALESCE(NULLIF(regexp_extract(path, '^(.*)/[^/]+$', 1), ''), '.') AS folder,
    category_directory,
    starts_with(path, '40_Archive/')                                   AS in_archive,
    size,
    size = 0                                                           AS is_empty,
    frontmatter ->> '$.type'                                           AS type,
    TRY_CAST(frontmatter ->> '$.created' AS DATE)                      AS created,
    frontmatter ->> '$.status'                                         AS status,
    updated_at                                                         AS modified_at,
    frontmatter
FROM notes;
COMMENT ON VIEW vault_notes IS 'Every note with type, created (date), status, folder, in_archive, is_empty and modified_at decoded; the base of the other views.';


-- ---------------------------------------------------------------------------
-- Evergreen notes
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW evergreen_notes AS
SELECT
    *,
    frontmatter ->> '$.domain'                                         AS domain,
    TRY_CAST(frontmatter ->> '$.ankified_date' AS DATE)                AS ankified_date
FROM vault_notes
WHERE type = 'evergreen';
COMMENT ON VIEW evergreen_notes IS 'Notes of type evergreen, with domain and ankified_date (NULL = not processed into Anki cards yet).';

-- Zero-byte Evergreen notes are knowledge-gap TODOs. They have no frontmatter, hence no type.
CREATE OR REPLACE VIEW evergreen_placeholders AS
SELECT *
FROM vault_notes
WHERE is_empty AND starts_with(path, '30_Resources/Evergreen/');
COMMENT ON VIEW evergreen_placeholders IS 'Zero-byte notes in the Evergreen folder: the knowledge-gap TODO placeholders.';


-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW project_notes AS
SELECT
    *,
    link_target(frontmatter ->> '$.lane')                              AS lane,
    TRY_CAST(frontmatter ->> '$.closed_date' AS DATE)                  AS closed_date,
    COALESCE(status IN ('active', 'paused'), FALSE)                    AS is_open,
    COALESCE(status IN ('done', 'abandoned'), FALSE)                   AS is_closed
FROM vault_notes
WHERE type = 'project';
COMMENT ON VIEW project_notes IS 'Notes of type project, with lane (the lane note name, decoded from the link), closed_date, is_open (active or paused) and is_closed (done or abandoned).';


-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW source_notes AS
SELECT
    *,
    frontmatter ->> '$.kind'                                           AS kind,
    frontmatter ->> '$.author'                                         AS author,
    frontmatter ->> '$.organization'                                   AS organization,
    TRY_CAST(frontmatter ->> '$.year' AS INTEGER)                      AS year,
    frontmatter ->> '$.url'                                            AS url,
    TRY_CAST(frontmatter ->> '$.consumed_on' AS DATE)                  AS consumed_on,
    TRY_CAST(frontmatter ->> '$.consumed_on' AS DATE) IS NOT NULL      AS is_consumed,
    -- NULL when the property is missing or is not a list of values.
    from_json(frontmatter -> '$.topics', '["VARCHAR"]')                AS topics
FROM vault_notes
WHERE type = 'source';
COMMENT ON VIEW source_notes IS 'Notes of type source, with kind, author, organization, year, url, consumed_on, is_consumed and topics (a list).';


-- ---------------------------------------------------------------------------
-- Daily notes
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW daily_notes AS
SELECT
    *,
    TRY_CAST(regexp_extract(name, '^(\d{4}-\d{2}-\d{2})$', 1) AS DATE) AS day
FROM vault_notes
WHERE type = 'daily_note';
COMMENT ON VIEW daily_notes IS 'Notes of type daily_note, with day parsed from the note name (NULL when the name is not YYYY-MM-DD). Group by folder to see where daily notes live.';


-- ---------------------------------------------------------------------------
-- Weekly-ish reviews: an irregular chain of explicit date ranges.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW weekly_reviews AS
WITH reviews AS (
    SELECT
        *,
        TRY_CAST(frontmatter ->> '$.start_range' AS DATE)              AS start_range,
        TRY_CAST(frontmatter ->> '$.end_range' AS DATE)                AS end_range
    FROM vault_notes
    WHERE type = 'weekly_review'
)
SELECT
    *,
    end_range - start_range + 1                                        AS days,
    lag(end_range) OVER chain                                          AS previous_end_range,
    -- 0 when the review starts the day after the previous one ended; negative when they overlap.
    start_range - lag(end_range) OVER chain - 1                        AS uncovered_days,
    row_number() OVER (ORDER BY end_range DESC NULLS LAST) = 1         AS is_latest
FROM reviews
WINDOW chain AS (ORDER BY end_range NULLS LAST);
COMMENT ON VIEW weekly_reviews IS 'Notes of type weekly_review with start_range, end_range, days, previous_end_range, uncovered_days (0 = contiguous with the previous review) and is_latest.';


-- ---------------------------------------------------------------------------
-- Review schedule: anything with an intended review cycle (lanes, roadmaps, ...).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW review_schedule AS
WITH scheduled AS (
    SELECT
        path, name, type, in_archive,
        frontmatter ->> '$.review_cycle'                               AS review_cycle,
        TRY_CAST(frontmatter ->> '$.last_review' AS DATE)              AS last_review
    FROM vault_notes
    WHERE frontmatter ->> '$.review_cycle' IS NOT NULL
)
SELECT
    *,
    last_review IS NULL                                                AS never_reviewed,
    current_date - last_review                                         AS days_since_review,
    -- A cycle is an intended cadence, not a strict SLA: these lengths are rough hints.
    CASE review_cycle WHEN 'weekly' THEN 7 WHEN 'monthly' THEN 31 END  AS cycle_days,
    (current_date - last_review) >
        CASE review_cycle WHEN 'weekly' THEN 7 WHEN 'monthly' THEN 31 END AS is_overdue
FROM scheduled;
COMMENT ON VIEW review_schedule IS 'Notes with a review_cycle: last_review, days_since_review, never_reviewed and a rough is_overdue hint (the cycle is an intention, not an SLA).';
