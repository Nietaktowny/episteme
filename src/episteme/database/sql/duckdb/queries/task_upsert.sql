INSERT INTO tasks (
    task_id, note_path, line_number, content, state, status_symbol, priority,
    created, start_date, scheduled_date, due_date, done_date, cancelled_date,
    recurring, on_completion, depends_on, source
) VALUES (
    $task_id, $note_path, $line_number, $content, $state, $status_symbol, $priority,
    $created, $start_date, $scheduled_date, $due_date, $done_date, $cancelled_date,
    $recurring, $on_completion, $depends_on, $source
)
ON CONFLICT (task_id) DO UPDATE SET
    note_path = excluded.note_path,
    line_number = excluded.line_number,
    content = excluded.content,
    state = excluded.state,
    status_symbol = excluded.status_symbol,
    priority = excluded.priority,
    created = excluded.created,
    start_date = excluded.start_date,
    scheduled_date = excluded.scheduled_date,
    due_date = excluded.due_date,
    done_date = excluded.done_date,
    cancelled_date = excluded.cancelled_date,
    recurring = excluded.recurring,
    on_completion = excluded.on_completion,
    depends_on = excluded.depends_on,
    source = excluded.source
