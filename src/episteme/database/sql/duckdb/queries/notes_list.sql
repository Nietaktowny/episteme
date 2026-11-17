-- The notes whose path contains $search (any case) and whose type is $type; NULL leaves a filter out. Newest first.
SELECT path, type, status, modified_at FROM vault_notes
WHERE ($search IS NULL OR path ILIKE '%' || $search || '%') AND ($type IS NULL OR type = $type)
ORDER BY modified_at DESC NULLS LAST, path
