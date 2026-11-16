SELECT
    (SELECT COUNT(*) FROM [{schema}].vault_notes)                           AS notes,
    (SELECT COUNT(*) FROM [{schema}].vault_notes WHERE in_archive = 1)      AS archived,
    (SELECT COUNT(*) FROM [{schema}].vault_notes WHERE is_empty = 1)        AS empty,
    COUNT(*)                                                     AS tasks,
    COUNT(CASE WHEN state = N'todo' THEN 1 END)                  AS todo,
    COUNT(CASE WHEN state = N'in_progress' THEN 1 END)           AS in_progress,
    COUNT(CASE WHEN state = N'done' THEN 1 END)                  AS done,
    COUNT(CASE WHEN state = N'cancelled' THEN 1 END)             AS cancelled,
    COUNT(CASE WHEN is_overdue = 1 THEN 1 END)                   AS overdue
FROM [{schema}].vault_tasks
