-- A hash of each note's body (the text after the frontmatter), computed while the vault is synced.
-- Comparing it between two states tells a real edit of a note from a change of its properties.
-- Notes stored before this migration have no hash until their next sync, which then counts them as
-- updated once.
ALTER TABLE notes ADD COLUMN body_hash TEXT;

-- vault_notes exposes the new column. CREATE OR REPLACE drops a view's comment, so it is set again.
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
    body_hash,
    frontmatter
FROM notes;
COMMENT ON VIEW vault_notes IS 'Every note with type, created (date), status, folder, in_archive, is_empty, modified_at and body_hash decoded; the base of the other views.';


-- The typed views select `*` from vault_notes, and DuckDB expands `*` when a view is created, so they
-- keep the old column list until they are created again. These are their definitions from V3, unchanged:
-- the typed views carry every column of vault_notes, body_hash included.

CREATE OR REPLACE VIEW evergreen_notes AS
SELECT
    *,
    frontmatter ->> '$.domain'                                         AS domain,
    TRY_CAST(frontmatter ->> '$.ankified_date' AS DATE)                AS ankified_date
FROM vault_notes
WHERE type = 'evergreen';
COMMENT ON VIEW evergreen_notes IS 'Notes of type evergreen, with domain and ankified_date (NULL = not processed into Anki cards yet).';

CREATE OR REPLACE VIEW evergreen_placeholders AS
SELECT *
FROM vault_notes
WHERE is_empty AND starts_with(path, '30_Resources/Evergreen/');
COMMENT ON VIEW evergreen_placeholders IS 'Zero-byte notes in the Evergreen folder: the knowledge-gap TODO placeholders.';

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

CREATE OR REPLACE VIEW daily_notes AS
SELECT
    *,
    TRY_CAST(regexp_extract(name, '^(\d{4}-\d{2}-\d{2})$', 1) AS DATE) AS day
FROM vault_notes
WHERE type = 'daily_note';
COMMENT ON VIEW daily_notes IS 'Notes of type daily_note, with day parsed from the note name (NULL when the name is not YYYY-MM-DD). Group by folder to see where daily notes live.';

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
