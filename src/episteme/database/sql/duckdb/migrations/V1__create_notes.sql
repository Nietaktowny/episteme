-- One row per markdown note; columns mirror MarkdownNote.
-- IF NOT EXISTS keeps this safe on databases created before migrations existed.
CREATE TABLE IF NOT EXISTS notes (
    path               TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    category_directory TEXT NOT NULL,
    size               BIGINT NOT NULL DEFAULT 0,
    created_at         TIMESTAMP NOT NULL,
    updated_at         TIMESTAMP NOT NULL,
    template           TEXT,
    template_version   DOUBLE,
    is_archived        BOOLEAN NOT NULL DEFAULT FALSE,
    archived_at        TIMESTAMP,
    frontmatter        JSON NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_notes_name ON notes (name);
CREATE INDEX IF NOT EXISTS idx_notes_category_directory ON notes (category_directory);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes (created_at);
CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes (updated_at);
