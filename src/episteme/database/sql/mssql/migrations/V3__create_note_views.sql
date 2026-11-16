-- Views over the notes table: the vault's conventions, decoded once. The same views, with the same names
-- and columns, as in the DuckDB schema (see sql/duckdb/migrations/V3), written in T-SQL. Where SQL Server
-- cannot say the same thing, the difference is:
--   * a flag (in_archive, is_open ...) is a BIT, so a query says WHERE in_archive = 1;
--   * a list (topics) is JSON text; OPENJSON turns it into rows;
--   * a view has no ORDER BY: SQL Server does not allow it, so the query that reads a view sorts;
--   * "today" is the date in UTC ([{schema}].today()), as Azure SQL runs in UTC.
--
-- The conventions are those of the PKM's property registry (Properties.md):
--   * `type` says what a note is, independently of where it lives, so the typed views filter on `type`;
--   * `created` is the semantic creation date, `modified_at` is the file's mtime;
--   * relationships are quoted Obsidian links ("[[Note]]"), decoded by [{schema}].link_target().
-- Views are bound when they are created: a migration that changes the `notes` table must create the views again.

-- The description of a view (the DuckDB schema uses COMMENT ON VIEW): an extended property of the view.
CREATE OR ALTER PROCEDURE [{schema}].episteme_describe_view @view SYSNAME, @description NVARCHAR(3500) AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (SELECT 1 FROM sys.extended_properties
               WHERE class = 1 AND minor_id = 0 AND name = N'MS_Description' AND major_id = OBJECT_ID(N'[{schema}].' + QUOTENAME(@view)))
        EXEC sp_updateextendedproperty N'MS_Description', @description, N'SCHEMA', N'{schema}', N'VIEW', @view;
    ELSE
        EXEC sp_addextendedproperty N'MS_Description', @description, N'SCHEMA', N'{schema}', N'VIEW', @view;
END
GO

-- The current date. One place to change if the days of the vault should not be UTC days.
CREATE OR ALTER FUNCTION [{schema}].today() RETURNS DATE AS
BEGIN
    RETURN CAST(SYSUTCDATETIME() AS DATE);
END
GO

-- The value at a path of a JSON document, as text, whatever it is: a string or number as itself, a list or an
-- object as JSON text; NULL when it is missing or null. (JSON_VALUE alone gives NULL for a list.) The text
-- is compared as it is written, like the DuckDB schema does.
CREATE OR ALTER FUNCTION [{schema}].json_text(@json NVARCHAR(MAX), @path NVARCHAR(4000)) RETURNS NVARCHAR(MAX) AS
BEGIN
    RETURN COALESCE(JSON_VALUE(@json, @path), JSON_QUERY(@json, @path)) COLLATE Latin1_General_100_BIN2;
END
GO

-- link_target('[[Note|alias]]') = 'Note'; also handles '[[Folder/Note#Heading]]'.
-- NULL for anything that is not an Obsidian link.
CREATE OR ALTER FUNCTION [{schema}].link_target(@link NVARCHAR(MAX)) RETURNS NVARCHAR(4000) AS
BEGIN
    IF @link IS NULL OR LEFT(@link, 2) <> N'[[' RETURN NULL;
    DECLARE @rest NVARCHAR(MAX) = SUBSTRING(@link, 3, LEN(@link + N'x'));
    DECLARE @end INT = LEN(@rest + N'x');
    DECLARE @at INT = CHARINDEX(N'|', @rest);
    IF @at > 0 AND @at < @end SET @end = @at;
    SET @at = CHARINDEX(N'#', @rest);
    IF @at > 0 AND @at < @end SET @end = @at;
    SET @at = CHARINDEX(N']', @rest);
    IF @at > 0 AND @at < @end SET @end = @at;
    RETURN NULLIF(LTRIM(RTRIM(LEFT(@rest, @end - 1))), N'') COLLATE Latin1_General_100_BIN2;
END
GO


-- ---------------------------------------------------------------------------
-- vault_notes: every note, with the universal properties decoded.
-- ---------------------------------------------------------------------------
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
    frontmatter
FROM [{schema}].notes;
GO
EXEC [{schema}].episteme_describe_view N'vault_notes', N'Every note with type, created (date), status, folder, in_archive, is_empty and modified_at decoded; the base of the other views.';
GO


-- ---------------------------------------------------------------------------
-- Evergreen notes
-- ---------------------------------------------------------------------------
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

-- Zero-byte Evergreen notes are knowledge-gap TODOs. They have no frontmatter, hence no type.
CREATE OR ALTER VIEW [{schema}].evergreen_placeholders AS
SELECT *
FROM [{schema}].vault_notes
WHERE is_empty = 1 AND path LIKE N'30\_Resources/Evergreen/%' ESCAPE N'\';
GO
EXEC [{schema}].episteme_describe_view N'evergreen_placeholders', N'Zero-byte notes in the Evergreen folder: the knowledge-gap TODO placeholders.';
GO


-- ---------------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------------
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


-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------
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


-- ---------------------------------------------------------------------------
-- Daily notes
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].daily_notes AS
SELECT
    *,
    CASE WHEN name LIKE N'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN TRY_CAST(name AS DATE) END AS [day]
FROM [{schema}].vault_notes
WHERE type = N'daily_note';
GO
EXEC [{schema}].episteme_describe_view N'daily_notes', N'Notes of type daily_note, with day parsed from the note name (NULL when the name is not YYYY-MM-DD). Group by folder to see where daily notes live.';
GO


-- ---------------------------------------------------------------------------
-- Weekly-ish reviews: an irregular chain of explicit date ranges.
-- ---------------------------------------------------------------------------
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


-- ---------------------------------------------------------------------------
-- Review schedule: anything with an intended review cycle (lanes, roadmaps, ...).
-- ---------------------------------------------------------------------------
CREATE OR ALTER VIEW [{schema}].review_schedule AS
WITH scheduled AS (
    SELECT
        path, name, type, in_archive,
        [{schema}].json_text(frontmatter, N'$.review_cycle')                   AS review_cycle,
        TRY_CAST([{schema}].json_text(frontmatter, N'$.last_review') AS DATE)  AS last_review
    FROM [{schema}].vault_notes
    WHERE [{schema}].json_text(frontmatter, N'$.review_cycle') IS NOT NULL
),
measured AS (
    SELECT
        *,
        DATEDIFF(day, last_review, [{schema}].today())                         AS days_since_review,
        -- A cycle is an intended cadence, not a strict SLA: these lengths are rough hints.
        CASE review_cycle WHEN N'weekly' THEN 7 WHEN N'monthly' THEN 31 END AS cycle_days
    FROM scheduled
)
SELECT
    path, name, type, in_archive, review_cycle, last_review,
    CAST(CASE WHEN last_review IS NULL THEN 1 ELSE 0 END AS BIT)        AS never_reviewed,
    days_since_review,
    cycle_days,
    CASE WHEN days_since_review IS NULL OR cycle_days IS NULL THEN NULL
         WHEN days_since_review > cycle_days THEN CAST(1 AS BIT)
         ELSE CAST(0 AS BIT) END                                        AS is_overdue
FROM measured;
GO
EXEC [{schema}].episteme_describe_view N'review_schedule', N'Notes with a review_cycle: last_review, days_since_review, never_reviewed and a rough is_overdue hint (the cycle is an intention, not an SLA).';
