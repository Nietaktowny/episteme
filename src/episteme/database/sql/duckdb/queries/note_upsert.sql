INSERT INTO notes (
    path, name, category_directory, size, created_at, updated_at,
    template, template_version, is_archived, archived_at, frontmatter
) VALUES (
    $path, $name, $category_directory, $size, $created_at, $updated_at,
    $template, $template_version, $is_archived, $archived_at, $frontmatter
)
ON CONFLICT (path) DO UPDATE SET
    name = excluded.name,
    category_directory = excluded.category_directory,
    size = excluded.size,
    created_at = excluded.created_at,
    updated_at = excluded.updated_at,
    template = excluded.template,
    template_version = excluded.template_version,
    is_archived = excluded.is_archived,
    archived_at = excluded.archived_at,
    frontmatter = excluded.frontmatter
