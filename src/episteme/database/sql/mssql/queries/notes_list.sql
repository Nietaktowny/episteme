-- The notes whose path contains $search (any case) and whose type is $type; NULL leaves a filter out. Newest first.
SELECT path, type, status, modified_at FROM [{schema}].vault_notes
WHERE ($search IS NULL OR LOWER(path) LIKE N'%' + LOWER($search) + N'%') AND ($type IS NULL OR type = $type)
ORDER BY CASE WHEN modified_at IS NULL THEN 1 ELSE 0 END, modified_at DESC, path
