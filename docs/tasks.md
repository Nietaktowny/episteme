# Tasks

Episteme reads the **tasks** of the vault: the markdown checkbox lines of the notes, written in the
format of the [Obsidian Tasks](https://publish.obsidian.md/tasks/) plugin (emoji metadata). They can be
parsed into [`Task`][episteme.models.obsidian.Task] objects, written back, changed in the notes, and summarized per note.

## The task format

```markdown
- [ ] Pay rent ⏫ 🔁 every month 📅 2026-12-01
- [x] Send invoice ➕ 2026-11-01 ✅ 2026-11-03
- [-] Old idea ❌ 2026-10-10
```

| Emoji | Field | Emoji | Field |
|---|---|---|---|
| 🔺 ⏫ 🔼 🔽 ⏬ | `priority` (highest … lowest) | ➕ | `created` |
| 🛫 | `start_date` | ⏳ | `scheduled_date` |
| 📅 | `due_date` | ✅ | `done_date` |
| ❌ | `cancelled_date` | 🔁 | `recurring` |
| 🏁 | `on_completion` | 🆔 | `id` |
| ⛔ | `depends_on` | | |

The symbol inside the checkbox is the **state**: `[ ]` todo, `[/]` in progress, `[x]` done, `[-]` cancelled,
`[~]` non-task. Any other symbol is a custom status of the plugin; it is a todo whose exact symbol is kept in
`status_symbol`, so it is written back unchanged.

## Parsing and writing

```python
task = Task.from_markdown("- [ ] Pay rent ⏫ 📅 2026-12-01", "Home.md")   # None if the line is not a task
task.content, task.priority, task.due_date      # 'Pay rent', Priority.HIGH, datetime(2026, 12, 1)
task.to_markdown()                              # '- [ ] Pay rent 🆔 k3x9ab ⏫ 📅 2026-12-01'
```

The metadata is read from the **end** of the line, so emoji inside the description stay in the text. A value
that is not valid, such as `📅 soon` or `📅 2026-13-45`, is left in the text as well and stops the parsing.
`to_markdown` writes the fields in the order the plugin does; it always uses `-` as the bullet. An id
that is not valid (letters, digits, `_` and `-`), such as `🆔 two words`, is left in the text too.

## What counts as a task

**In this PKM a task has an id.** A checkbox without a 🆔 written in its note is just a checkbox, not a task.
This is a rule of the author's PKM, and it is a deliberate simplification: it gives every task a stable
identity (the database is keyed by it) and it keeps ordinary checklists out of the task system. **If the rule
ever changes, the default below and the key of the `tasks` table need a revision.**

So by default only tasks with an id written in their note are returned, and only those are stored in the
database. It is a setting, not a limit:

```python
episteme.obsidian.scan_for_tasks()                     # tasks with an id (the default)
episteme.obsidian.scan_for_tasks(require_id=False)     # every checkbox
manager.get_tasks_from_note(note, require_id=False)    # the same, for one note

ObsidianTasksManager(require_id=False)                 # a manager that returns every checkbox by default
episteme.obsidian.tasks.require_id = False             # or change the one of the vault
```

A checkbox becomes a task when its id is saved: `update_task` writes the id of a task read without one
(see below), so the next scan returns it. `Task.id_saved` tells whether the id is written in the note.

## Id, creation date and priority

| | A new task | A task read from a note |
|---|---|---|
| `id` (🆔) | a new random id | the id of the line; **if it has none, a new one is made** |
| `created` (➕) | today | the date of the line, else `None` |
| `priority` | `Priority.LOW` | the priority of the line, else `None` |

The three fields are not treated the same way:

- **The id is always there.** A task read without one (see `require_id=False`) gets an id in memory, so it can be referred to at once.
  The note is *not* touched by reading: the id is written to the line the next time the task is **saved**
  (`update_task`, `create_task`), together with the other changes. Until then the id is not stable, and
  reading the note again makes a different one.
- **The creation date and the priority are only defaults of new tasks.** They are nullable; a task read from
  a note without them keeps `None`, and saving it does not add them. Only tasks we create have them.
  To drop one from a new task, give `None`: `Task(..., created=None, priority=None)`.

## Scanning the vault

```python
tasks = episteme.obsidian.scan_for_tasks()                 # every task of the vault
tasks = episteme.obsidian.scan_for_tasks(summary=True)     # ...and print the summary table
```

The scan is meant to survive a messy vault:

- tasks may be **indented** (nested lists) and use `-`, `*` or `+` as the bullet;
- the **frontmatter** and **fenced code blocks** (```` ``` ```` and `~~~`) are not searched;
- a note that cannot be read (deleted meanwhile, not valid UTF-8) is **logged and skipped**; it does not
  stop the scan.

## The summary table

`summary=True` prints one row per note that has tasks, a column per state, the total, and the number of
**overdue** tasks (still open, due before today), with a last row that sums every column:

```text
                                 Tasks per note
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━┓
┃ Note              ┃ todo ┃ in progress ┃ done ┃ cancelled ┃ non-task ┃ total ┃ overdue ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━┩
│ 10_Projects/p.md  │    0 │           1 │    0 │         0 │        0 │     1 │       1 │
│ TODOs.md          │    1 │           0 │    1 │         0 │        0 │     2 │       0 │
│ Total             │    1 │           1 │    1 │         0 │        0 │     3 │       1 │
└───────────────────┴──────┴─────────────┴──────┴───────────┴──────────┴───────┴─────────┘
```

The same table can be built from any list of tasks with `ObsidianTasksManager.summary_table(tasks, today=...)`,
and `ObsidianTasksManager.count_by_note(tasks)` gives the plain counts per note and state.

## In the database

The tasks are copied into the `tasks` table by `episteme.sync_obsidian()` (and `episteme.sync()`), together
with the notes and in the same transaction, so a failed sync changes neither. **The table is a derived copy:
the markdown files stay the only source of truth, and nothing writes to the table but the sync.**

```python
episteme.sync()
for task in episteme.data.iter_tasks():            # ordered by note and line; episteme.data.iter_tasks("TODOs.md") for one note
    print(task.id, task.content, task.due_date)
episteme.data.count_tasks()
```

- **Only tasks with an id** written in their note are stored (see above). The id is the key: a task that
  moves to another line or another note is *updated*, not removed and added again.
- **Duplicate ids** (a copy-pasted line) cannot all be stored. The first one is, the rest are skipped, logged
  and listed in `result.duplicate_task_ids`; give the copy a new id.
- **The sync result** has `tasks_added`, `tasks_updated`, `tasks_unchanged` and `tasks_removed`, and shows
  them in its panel when there are tasks. A sync that is given no tasks (`sync_notes(notes)`) leaves the
  stored ones alone.
- **Removing a note removes its tasks.**
- **Dates are days** (`DATE`); `state` is `todo`, `in_progress`, `done`, `cancelled` or `non_task`; `priority`
  is `highest` … `lowest`, or `NULL` for a task without one.
- A task read from the database carries its `source` and `line_number`, so it can be passed to `update_task`,
  which finds its line in the note again, or raises `StaleTaskError` if the note changed since the sync.
- **The copy lags**: a change made through `create_task`, `update_task` or `delete_task`, or in Obsidian,
  reaches the table on the next sync (every minute, if the scheduler job runs).

The views (`vault_tasks`, `open_tasks`, `overdue_tasks`, `tasks_per_note`) are described on
[Database views](views.md), with examples. The table and the views come from migrations `V6` and `V7`; a
database from before them keeps its notes and fills the tasks on the first sync.

## Changing tasks

```python
manager = episteme.obsidian.tasks
note = episteme.obsidian["TODOs"]

task = manager.create_task(note, "Buy milk")          # appended at the end; text or a Task, written with id, date and priority

task = manager.get_tasks_from_note(note)[0]
task = manager.update_task(note, task.complete())     # rewrites that one line (see "Editing helpers" below)
manager.delete_task(note, task)                       # removes it
```

**The markdown file is the only source of truth.** A task has no identity of its own, and the note may
be open in Obsidian, so Episteme never works from a stale copy. A task read from a note remembers where it
came from, its `line_number` and the exact line (`source`). To update or delete it, Episteme looks for that
line in the file *as it is now*: at the remembered position first, else anywhere if it is the only such line.

- Lines added above are fine: the task is still found.
- If the line was edited in Obsidian, removed, or now appears more than once somewhere else, `StaleTaskError`
  is raised and **nothing is changed**. Read the note again and retry.
- A task that was not read from a note (`source` is `None`) cannot be updated; add it with `create_task`.
- A task of another note raises `ValueError`.
- An update writes the `id` of the task: a task that had none on disk gets it now.

Only the one line is rewritten. The rest of the file is kept byte for byte, including the frontmatter,
the line endings (`\n` or `\r\n`), and the indentation and bullet of the task. The file is replaced
atomically (written to a temporary file, then renamed), the permissions are kept, and nothing is written
if the file changed while it was being edited.

`update_task` returns the task with its new `source`, so it can be updated again. The notes cache their content;
after a change it is dropped and read again on next access. The database still shows the old text until the
next sync.

!!! note "The plugin's rules are not applied"
    `update_task` writes exactly the fields of the task it is given. It does not set `done_date` for you
    (`task.complete()` does) and does not create the next occurrence of a recurring task.

## Editing helpers

Used with `update_task` above. To change a task there is no need to import `TaskState`, `Priority` or `datetime`: the helpers take plain
names and dates, and the IDE completes them (`Literal` types for the names).

```python
task.set_state("in_progress")       # "todo" | "in_progress" | "done" | "cancelled" | "non_task"
task.set_priority("high")           # "highest" | "high" | "medium" | "low" | "lowest", or None for none

task.set_due("2026-12-01")                          # text YYYY-MM-DD, a date or a datetime
task.set_due(year=2026, month=12, day=1)            # the day written out
task.set_due(in_days=3)                             # relative to today; negative for the past
task.set_due()                                      # no argument (or None) clears the date
task.set_scheduled(...)  # ⏳, same arguments        task.set_start(...)  # 🛫, same arguments

task.start()                                        # in progress
task.complete()                                     # done, ✅ today;      complete("2026-12-02") for another day
task.cancel()                                       # cancelled, ❌ today
task.reopen()                                       # todo again, no ✅ / ❌
```

- They change the task **in place and return it**, so they chain:
  `task.set_priority("high").set_due(in_days=3)`.
- Only `complete`, `cancel` and `reopen` touch the dates of the end of the task; `set_state` changes the state alone.
  Changing the state drops a custom checkbox symbol, since it belonged to the old state.
- A date is given in one way only (`ValueError` otherwise), and an invalid one (`"tomorrow"`, `2026-02-30`)
  is a `ValueError` as well. The time of a `datetime` is dropped: the note keeps days.
- They change the task in memory. To write it to the note, call `update_task`:

```python
manager.update_task(note, task.set_priority("high").complete())
```

`complete` does not create the next occurrence of a recurring task.

