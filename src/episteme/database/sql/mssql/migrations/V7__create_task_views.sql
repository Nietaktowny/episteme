-- Views over the tasks table. Like the note views they decode the conventions once, so queries do not have to.

CREATE OR ALTER VIEW [{schema}].vault_tasks AS
SELECT
    t.task_id,
    t.note_path,
    n.name                                                              AS note_name,
    n.category_directory,
    n.in_archive,
    t.line_number,
    t.content,
    t.state,
    t.status_symbol,
    t.priority,
    t.created,
    t.start_date,
    t.scheduled_date,
    t.due_date,
    t.done_date,
    t.cancelled_date,
    t.recurring,
    t.on_completion,
    t.depends_on,
    CAST(CASE WHEN t.state IN (N'todo', N'in_progress') THEN 1 ELSE 0 END AS BIT) AS is_open,
    CAST(CASE WHEN t.state IN (N'todo', N'in_progress') AND t.due_date < [{schema}].today() THEN 1 ELSE 0 END AS BIT) AS is_overdue,
    CASE WHEN t.state IN (N'todo', N'in_progress') AND t.due_date < [{schema}].today()
         THEN DATEDIFF(day, t.due_date, [{schema}].today()) END                AS days_overdue
FROM [{schema}].tasks AS t
JOIN [{schema}].vault_notes AS n ON n.path = t.note_path;
GO
EXEC [{schema}].episteme_describe_view N'vault_tasks', N'Every task (a checkbox with an id) with its note_name, category_directory and in_archive, plus is_open (todo or in progress), is_overdue and days_overdue (open and due before today).';
GO

CREATE OR ALTER VIEW [{schema}].open_tasks AS
SELECT * FROM [{schema}].vault_tasks WHERE is_open = 1;
GO
EXEC [{schema}].episteme_describe_view N'open_tasks', N'Tasks that are todo or in progress.';
GO

CREATE OR ALTER VIEW [{schema}].overdue_tasks AS
SELECT * FROM [{schema}].vault_tasks WHERE is_overdue = 1;
GO
EXEC [{schema}].episteme_describe_view N'overdue_tasks', N'Open tasks whose due date is before today, with days_overdue.';
GO

CREATE OR ALTER VIEW [{schema}].tasks_per_note AS
SELECT
    note_path,
    note_name,
    COUNT(CASE WHEN state = N'todo' THEN 1 END)        AS todo,
    COUNT(CASE WHEN state = N'in_progress' THEN 1 END) AS in_progress,
    COUNT(CASE WHEN state = N'done' THEN 1 END)        AS done,
    COUNT(CASE WHEN state = N'cancelled' THEN 1 END)   AS cancelled,
    COUNT(CASE WHEN state = N'non_task' THEN 1 END)    AS non_task,
    COUNT(*)                                           AS total,
    COUNT(CASE WHEN is_overdue = 1 THEN 1 END)         AS overdue
FROM [{schema}].vault_tasks
GROUP BY note_path, note_name;
GO
EXEC [{schema}].episteme_describe_view N'tasks_per_note', N'Per note: the number of tasks in each state, the total and how many are overdue.';
