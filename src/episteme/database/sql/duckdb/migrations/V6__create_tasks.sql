-- One row per task of the vault: a derived copy of the checkbox lines of the notes, rebuilt by every sync.
-- The markdown file is the only source of truth; nothing writes here except the sync.
--
-- Only tasks that have an id written in their note (🆔) are stored: in this PKM a checkbox without an id is
-- not a task. The id is therefore stable, and it is the key; moving a task to another line or note changes
-- note_path and line_number, not its identity. If this rule ever changes, this table's key needs a revision.
--
-- Dates are days, as in the notes. state is todo, in_progress, done, cancelled or non_task; priority is
-- highest, high, medium, low or lowest, and NULL when the task has none (the plugin's 'normal').
-- source is the exact line, so a task read back can be found in its note again to be updated.
CREATE TABLE tasks (
    task_id        TEXT PRIMARY KEY,
    note_path      TEXT NOT NULL,
    line_number    INTEGER NOT NULL,
    content        TEXT NOT NULL,
    state          TEXT NOT NULL,
    status_symbol  TEXT,
    priority       TEXT,
    created        DATE,
    start_date     DATE,
    scheduled_date DATE,
    due_date       DATE,
    done_date      DATE,
    cancelled_date DATE,
    recurring      TEXT,
    on_completion  TEXT,
    depends_on     TEXT,
    source         TEXT NOT NULL
);
