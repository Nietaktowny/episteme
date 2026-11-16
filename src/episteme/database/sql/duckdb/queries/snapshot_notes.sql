-- The state of the vault worth keeping: what a later review compares against.
-- Only what is the same on every machine: no file times, no absolute paths.
SELECT
    path,
    name,
    in_archive,
    is_empty,
    size,
    type,
    created,
    status,
    body_hash,
    frontmatter::VARCHAR AS frontmatter
FROM vault_notes
ORDER BY path
