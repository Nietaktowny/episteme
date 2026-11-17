-- The paths of the notes whose path or type contains $search (any case); every path when $search is NULL.
SELECT path FROM vault_notes
WHERE $search IS NULL OR path ILIKE '%' || $search || '%' OR type ILIKE '%' || $search || '%'
ORDER BY path
