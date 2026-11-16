-- One row per markdown note; columns mirror MarkdownNote.
--
-- Text that is compared as it is written (paths, names) uses a binary collation, as DuckDB compares text:
-- the default collation of SQL Server ignores case, so 'Note.md' and 'note.md' would be the same note.
-- frontmatter is JSON text (SQL Server has no JSON column in every version); ISJSON keeps it valid.
CREATE TABLE [{schema}].notes (
    path               NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    name               NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL,
    category_directory NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL,
    size               BIGINT        NOT NULL DEFAULT 0,
    created_at         DATETIME2(6)  NOT NULL,
    updated_at         DATETIME2(6)  NOT NULL,
    template           NVARCHAR(450) NULL,
    template_version   FLOAT         NULL,
    is_archived        BIT           NOT NULL DEFAULT 0,
    archived_at        DATETIME2(6)  NULL,
    frontmatter        NVARCHAR(MAX) NOT NULL DEFAULT N'{}' CONSTRAINT ck_notes_frontmatter CHECK (ISJSON(frontmatter) = 1)
);
GO

CREATE INDEX idx_notes_name ON [{schema}].notes (name);
CREATE INDEX idx_notes_category_directory ON [{schema}].notes (category_directory);
CREATE INDEX idx_notes_created_at ON [{schema}].notes (created_at);
CREATE INDEX idx_notes_updated_at ON [{schema}].notes (updated_at);
