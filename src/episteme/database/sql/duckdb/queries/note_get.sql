SELECT path, name, category_directory, size, created_at, updated_at,
       template, template_version, is_archived, archived_at, frontmatter, body_hash, type, created
FROM notes
WHERE path = $path
