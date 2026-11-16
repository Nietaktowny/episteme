MERGE [{schema}].tasks WITH (HOLDLOCK) AS target
USING (SELECT
    CAST($task_id AS NVARCHAR(450))        AS task_id,
    CAST($note_path AS NVARCHAR(450))      AS note_path,
    CAST($line_number AS INT)              AS line_number,
    CAST($content AS NVARCHAR(MAX))        AS content,
    CAST($state AS NVARCHAR(50))           AS state,
    CAST($status_symbol AS NVARCHAR(50))   AS status_symbol,
    CAST($priority AS NVARCHAR(50))        AS priority,
    CAST($created AS DATE)                 AS created,
    CAST($start_date AS DATE)              AS start_date,
    CAST($scheduled_date AS DATE)          AS scheduled_date,
    CAST($due_date AS DATE)                AS due_date,
    CAST($done_date AS DATE)               AS done_date,
    CAST($cancelled_date AS DATE)          AS cancelled_date,
    CAST($recurring AS NVARCHAR(MAX))      AS recurring,
    CAST($on_completion AS NVARCHAR(MAX))  AS on_completion,
    CAST($depends_on AS NVARCHAR(MAX))     AS depends_on,
    CAST($source AS NVARCHAR(MAX))         AS source
) AS source ON target.task_id = source.task_id
WHEN MATCHED THEN UPDATE SET
    note_path = source.note_path, line_number = source.line_number, content = source.content,
    state = source.state, status_symbol = source.status_symbol, priority = source.priority,
    created = source.created, start_date = source.start_date, scheduled_date = source.scheduled_date,
    due_date = source.due_date, done_date = source.done_date, cancelled_date = source.cancelled_date,
    recurring = source.recurring, on_completion = source.on_completion, depends_on = source.depends_on,
    source = source.source
WHEN NOT MATCHED THEN INSERT (
    task_id, note_path, line_number, content, state, status_symbol, priority,
    created, start_date, scheduled_date, due_date, done_date, cancelled_date,
    recurring, on_completion, depends_on, source
) VALUES (
    source.task_id, source.note_path, source.line_number, source.content, source.state, source.status_symbol, source.priority,
    source.created, source.start_date, source.scheduled_date, source.due_date, source.done_date, source.cancelled_date,
    source.recurring, source.on_completion, source.depends_on, source.source
);
