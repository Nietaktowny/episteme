MERGE [{schema}].notes WITH (HOLDLOCK) AS target
USING (SELECT
    CAST($path AS NVARCHAR(450))               AS path,
    CAST($name AS NVARCHAR(450))               AS name,
    CAST($category_directory AS NVARCHAR(450)) AS category_directory,
    CAST($size AS BIGINT)                      AS size,
    CAST($created_at AS DATETIME2(6))          AS created_at,
    CAST($updated_at AS DATETIME2(6))          AS updated_at,
    CAST($template AS NVARCHAR(450))           AS template,
    CAST($template_version AS FLOAT)           AS template_version,
    CAST($is_archived AS BIT)                  AS is_archived,
    CAST($archived_at AS DATETIME2(6))         AS archived_at,
    CAST($frontmatter AS NVARCHAR(MAX))        AS frontmatter,
    CAST($body_hash AS NVARCHAR(100))          AS body_hash
) AS source ON target.path = source.path
WHEN MATCHED THEN UPDATE SET
    name = source.name,
    category_directory = source.category_directory,
    size = source.size,
    created_at = source.created_at,
    updated_at = source.updated_at,
    template = source.template,
    template_version = source.template_version,
    is_archived = source.is_archived,
    archived_at = source.archived_at,
    frontmatter = source.frontmatter,
    body_hash = source.body_hash
WHEN NOT MATCHED THEN INSERT (
    path, name, category_directory, size, created_at, updated_at,
    template, template_version, is_archived, archived_at, frontmatter, body_hash
) VALUES (
    source.path, source.name, source.category_directory, source.size, source.created_at, source.updated_at,
    source.template, source.template_version, source.is_archived, source.archived_at, source.frontmatter, source.body_hash
);
