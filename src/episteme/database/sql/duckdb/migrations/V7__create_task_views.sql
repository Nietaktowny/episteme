-- Views over the tasks table. Like the note views they decode the conventions once, so queries do not have to.

CREATE VIEW vault_tasks AS
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
    t.state IN ('todo', 'in_progress')                                  AS is_open,
    COALESCE(t.state IN ('todo', 'in_progress') AND t.due_date < current_date, FALSE) AS is_overdue,
    CASE WHEN t.state IN ('todo', 'in_progress') AND t.due_date < current_date
         THEN date_diff('day', t.due_date, current_date) END            AS days_overdue
FROM tasks AS t
JOIN vault_notes AS n ON n.path = t.note_path;
COMMENT ON VIEW vault_tasks IS 'Every task (a checkbox with an id) with its note_name, category_directory and in_archive, plus is_open (todo or in progress), is_overdue and days_overdue (open and due before today).';

CREATE VIEW open_tasks AS
SELECT * FROM vault_tasks WHERE is_open;
COMMENT ON VIEW open_tasks IS 'Tasks that are todo or in progress.';

CREATE VIEW overdue_tasks AS
SELECT * FROM vault_tasks WHERE is_overdue;
COMMENT ON VIEW overdue_tasks IS 'Open tasks whose due date is before today, with days_overdue.';

CREATE VIEW tasks_per_note AS
SELECT
    note_path,
    note_name,
    count(*) FILTER (WHERE state = 'todo')        AS todo,
    count(*) FILTER (WHERE state = 'in_progress') AS in_progress,
    count(*) FILTER (WHERE state = 'done')        AS done,
    count(*) FILTER (WHERE state = 'cancelled')   AS cancelled,
    count(*) FILTER (WHERE state = 'non_task')    AS non_task,
    count(*)                                      AS total,
    count(*) FILTER (WHERE is_overdue)            AS overdue
FROM vault_tasks
GROUP BY note_path, note_name
ORDER BY note_path;
COMMENT ON VIEW tasks_per_note IS 'Per note: the number of tasks in each state, the total and how many are overdue.';
