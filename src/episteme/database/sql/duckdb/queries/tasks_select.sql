-- $note_path may be NULL to select the tasks of every note.
SELECT task_id, note_path, line_number, content, state, status_symbol, priority,
       created, start_date, scheduled_date, due_date, done_date, cancelled_date,
       recurring, on_completion, depends_on, source
FROM tasks
WHERE $note_path IS NULL OR note_path = $note_path
ORDER BY note_path, line_number
