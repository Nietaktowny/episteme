SELECT
    (SELECT count(*) FROM vault_notes)                       AS notes,
    (SELECT count(*) FROM vault_notes WHERE in_archive)      AS archived,
    (SELECT count(*) FROM vault_notes WHERE is_empty)        AS empty,
    count(*)                                                 AS tasks,
    count(*) FILTER (WHERE state = 'todo')                   AS todo,
    count(*) FILTER (WHERE state = 'in_progress')            AS in_progress,
    count(*) FILTER (WHERE state = 'done')                   AS done,
    count(*) FILTER (WHERE state = 'cancelled')              AS cancelled,
    count(*) FILTER (WHERE is_overdue)                       AS overdue
FROM vault_tasks
