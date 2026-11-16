-- A hash of each note's body (the text after the frontmatter), computed while the vault is synced.
-- Comparing it between two states tells a real edit of a note from a change of its properties.
-- Notes stored before this migration have no hash until their next sync, which then counts them as
-- updated once.
ALTER TABLE [{schema}].notes ADD body_hash NVARCHAR(100) NULL;
GO

-- vault_notes exposes the new column.
CREATE OR ALTER VIEW [{schema}].vault_notes AS
SELECT
    path,
    name,
    CASE WHEN CHARINDEX(N'/', path) > 0
         THEN LEFT(path, LEN(path) - CHARINDEX(N'/', REVERSE(path)))
         ELSE N'.' END                                                  AS folder,
    category_directory,
    CAST(CASE WHEN path LIKE N'40\_Archive/%' ESCAPE N'\' THEN 1 ELSE 0 END AS BIT) AS in_archive,
    size,
    CAST(CASE WHEN size = 0 THEN 1 ELSE 0 END AS BIT)                   AS is_empty,
    [{schema}].json_text(frontmatter, N'$.type')                               AS type,
    TRY_CAST([{schema}].json_text(frontmatter, N'$.created') AS DATE)          AS created,
    [{schema}].json_text(frontmatter, N'$.status')                             AS status,
    updated_at                                                          AS modified_at,
    body_hash,
    frontmatter
FROM [{schema}].notes;
GO
EXEC [{schema}].episteme_describe_view N'vault_notes', N'Every note with type, created (date), status, folder, in_archive, is_empty, modified_at and body_hash decoded; the base of the other views.';
GO

-- The typed views select `*` from vault_notes, which is expanded when a view is created, so they keep the old
-- column list until they are created again. These are their definitions from V3, unchanged: the typed views
-- carry every column of vault_notes, body_hash included.

CREATE OR ALTER VIEW [{schema}].evergreen_notes AS
SELECT
    *,
    [{schema}].json_text(frontmatter, N'$.domain')                             AS domain,
    TRY_CAST([{schema}].json_text(frontmatter, N'$.ankified_date') AS DATE)    AS ankified_date
FROM [{schema}].vault_notes
WHERE type = N'evergreen';
GO
EXEC [{schema}].episteme_describe_view N'evergreen_notes', N'Notes of type evergreen, with domain and ankified_date (NULL = not processed into Anki cards yet).';
GO

CREATE OR ALTER VIEW [{schema}].evergreen_placeholders AS
SELECT *
FROM [{schema}].vault_notes
WHERE is_empty = 1 AND path LIKE N'30\_Resources/Evergreen/%' ESCAPE N'\';
GO
EXEC [{schema}].episteme_describe_view N'evergreen_placeholders', N'Zero-byte notes in the Evergreen folder: the knowledge-gap TODO placeholders.';
GO

CREATE OR ALTER VIEW [{schema}].project_notes AS
SELECT
    *,
    [{schema}].link_target([{schema}].json_text(frontmatter, N'$.lane'))              AS lane,
    TRY_CAST([{schema}].json_text(frontmatter, N'$.closed_date') AS DATE)      AS closed_date,
    CAST(CASE WHEN status IN (N'active', N'paused') THEN 1 ELSE 0 END AS BIT)  AS is_open,
    CAST(CASE WHEN status IN (N'done', N'abandoned') THEN 1 ELSE 0 END AS BIT) AS is_closed
FROM [{schema}].vault_notes
WHERE type = N'project';
GO
EXEC [{schema}].episteme_describe_view N'project_notes', N'Notes of type project, with lane (the lane note name, decoded from the link), closed_date, is_open (active or paused) and is_closed (done or abandoned).';
GO

CREATE OR ALTER VIEW [{schema}].source_notes AS
SELECT
    v.*,
    [{schema}].json_text(v.frontmatter, N'$.kind')                             AS kind,
    [{schema}].json_text(v.frontmatter, N'$.author')                           AS author,
    [{schema}].json_text(v.frontmatter, N'$.organization')                     AS organization,
    TRY_CAST([{schema}].json_text(v.frontmatter, N'$.year') AS INT)            AS [year],
    [{schema}].json_text(v.frontmatter, N'$.url')                              AS url,
    TRY_CAST([{schema}].json_text(v.frontmatter, N'$.consumed_on') AS DATE)    AS consumed_on,
    CAST(CASE WHEN TRY_CAST([{schema}].json_text(v.frontmatter, N'$.consumed_on') AS DATE) IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS is_consumed,
    -- JSON text of the list; NULL when the property is missing or is not a list.
    t.topics                                                            AS topics
FROM [{schema}].vault_notes AS v
CROSS APPLY (SELECT CASE WHEN LEFT(LTRIM(JSON_QUERY(v.frontmatter, N'$.topics')), 1) = N'[' THEN JSON_QUERY(v.frontmatter, N'$.topics') END AS topics) AS t
WHERE v.type = N'source';
GO
EXEC [{schema}].episteme_describe_view N'source_notes', N'Notes of type source, with kind, author, organization, year, url, consumed_on, is_consumed and topics (JSON text of a list).';
GO

CREATE OR ALTER VIEW [{schema}].daily_notes AS
SELECT
    *,
    CASE WHEN name LIKE N'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN TRY_CAST(name AS DATE) END AS [day]
FROM [{schema}].vault_notes
WHERE type = N'daily_note';
GO
EXEC [{schema}].episteme_describe_view N'daily_notes', N'Notes of type daily_note, with day parsed from the note name (NULL when the name is not YYYY-MM-DD). Group by folder to see where daily notes live.';
GO

CREATE OR ALTER VIEW [{schema}].weekly_reviews AS
WITH reviews AS (
    SELECT
        *,
        TRY_CAST([{schema}].json_text(frontmatter, N'$.start_range') AS DATE)  AS start_range,
        TRY_CAST([{schema}].json_text(frontmatter, N'$.end_range') AS DATE)    AS end_range
    FROM [{schema}].vault_notes
    WHERE type = N'weekly_review'
)
SELECT
    *,
    DATEDIFF(day, start_range, end_range) + 1                           AS days,
    LAG(end_range) OVER (ORDER BY CASE WHEN end_range IS NULL THEN 1 ELSE 0 END, end_range) AS previous_end_range,
    -- 0 when the review starts the day after the previous one ended; negative when they overlap.
    DATEDIFF(day, LAG(end_range) OVER (ORDER BY CASE WHEN end_range IS NULL THEN 1 ELSE 0 END, end_range), start_range) - 1 AS uncovered_days,
    CAST(CASE WHEN ROW_NUMBER() OVER (ORDER BY CASE WHEN end_range IS NULL THEN 1 ELSE 0 END, end_range DESC) = 1 THEN 1 ELSE 0 END AS BIT) AS is_latest
FROM reviews;
GO
EXEC [{schema}].episteme_describe_view N'weekly_reviews', N'Notes of type weekly_review with start_range, end_range, days, previous_end_range, uncovered_days (0 = contiguous with the previous review) and is_latest.';
GO
