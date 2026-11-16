-- One row per task of the vault: a derived copy of the checkbox lines of the notes, rebuilt by every sync.
-- The markdown file is the only source of truth; nothing writes here except the sync.
--
-- Only tasks that have an id written in their note are stored: in this PKM a checkbox without an id is
-- not a task. The id is therefore stable, and it is the key; moving a task to another line or note changes
-- note_path and line_number, not its identity.
--
-- Dates are days, as in the notes. state is todo, in_progress, done, cancelled or non_task; priority is
-- highest, high, medium, low or lowest, and NULL when the task has none.
-- source is the exact line, so a task read back can be found in its note again to be updated.
CREATE TABLE [{schema}].tasks (
    task_id        NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
    note_path      NVARCHAR(450) COLLATE Latin1_General_100_BIN2 NOT NULL,
    line_number    INT           NOT NULL,
    content        NVARCHAR(MAX) NOT NULL,
    state          NVARCHAR(50)  NOT NULL,
    status_symbol  NVARCHAR(50)  NULL,
    priority       NVARCHAR(50)  NULL,
    created        DATE          NULL,
    start_date     DATE          NULL,
    scheduled_date DATE          NULL,
    due_date       DATE          NULL,
    done_date      DATE          NULL,
    cancelled_date DATE          NULL,
    recurring      NVARCHAR(MAX) NULL,
    on_completion  NVARCHAR(MAX) NULL,
    depends_on     NVARCHAR(MAX) NULL,
    source         NVARCHAR(MAX) NOT NULL
);
