-- The paths of the notes whose path or type contains $search (any case); every path when $search is NULL.
-- The text columns are compared case-sensitively (binary collation), so both sides are lowered.
SELECT path FROM [{schema}].vault_notes
WHERE $search IS NULL OR LOWER(path) LIKE N'%' + LOWER($search) + N'%' OR LOWER(type) LIKE N'%' + LOWER($search) + N'%'
ORDER BY path
