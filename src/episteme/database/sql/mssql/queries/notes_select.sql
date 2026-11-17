-- $category_directory may be NULL to select notes of every category.
SELECT path, name, category_directory, size, created_at, updated_at,
       template, template_version, is_archived, archived_at, frontmatter, body_hash, type, created
FROM [{schema}].notes
WHERE $category_directory IS NULL OR category_directory = $category_directory
ORDER BY path
