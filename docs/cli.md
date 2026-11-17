# Command line

```console
$ episteme --help
$ episteme doctor                         # is everything set up?
$ episteme status                         # what the database holds
$ episteme sync [obsidian|anki]           # into the database
$ episteme obsidian read NOTE [--raw] [--body]   # a note, by path or name
$ episteme obsidian notes [TEXT] [--type T]      # list notes (database)
$ episteme obsidian templates | new TEMPLATE NAME [-p DIR] | stats
$ episteme health [--severity S|--rule R|--fail-on S]   |   health check   |   health rules
$ episteme tasks list [--open|--overdue|--state S|--note N|--fresh|--summary]
$ episteme tasks show ID | add NOTE "text" [--due D -p high] | update ID [-s S --due D ...] | done ID | delete ID
$ episteme anki cards [-t TAG]... [-d DECK] [-s STATE] [--due] [--leeches] [-q TEXT] [--where SQL] [--content]
$ episteme anki info | decks | content NOTE_ID...
$ episteme db info | views | query "SELECT ..." | clear [--keep-anki|--keep-obsidian] [--yes]
$ episteme snapshot create [LABEL] [--overwrite] | list
$ episteme schedule run --sync "0 6 * * *" --snapshot "@weekly"
$ episteme schedule jobs                  # what would run, and when next
$ episteme config init [-o FILE] | show | envs
$ episteme tui
```

Global options go before the command: `--env`/`--env-file`, `--vault`, `--db` (override the settings),
`--json` (data for scripts instead of tables), `-v`/`-vv` (logging), `--debug` (tracebacks).
A failure exits with status 1 and one line on stderr.

## Several configurations

```console
$ episteme config init -o .env.work       # an example with every setting at its default
$ episteme --env work status              # reads ./.env.work
$ episteme --env-file ~/vaults/test.env status
$ EPISTEME_ENV=work episteme status       # EPISTEME_ENV / EPISTEME_ENV_FILE also work
$ episteme config envs                    # the env files here, and the one in use
```

Without either option `./.env` is read. `--vault` and `--db` override whichever file is used.

## Tasks

Tasks live in the notes. `tasks list` and `show` read them (from the database, which is as of the
last sync, and from the note), and `add`, `update`, `done` and `delete` write the note's file. The
database follows at the next `sync`, or at once with `--sync`. Dates are `YYYY-MM-DD`; `none`
removes one (`--due none`). `delete` asks first unless `--yes`.

## Scheduling

Each job has its own cron expression (five fields, or `@hourly`, `@daily`, `@weekly`, `@monthly`),
and a job without one does not run:

```console
$ episteme schedule run --sync "0 6 * * *" --snapshot "0 22 * * 0"    # sync daily, snapshot on Sundays
$ episteme schedule run                                              # schedules from the settings
$ episteme schedule run --sync-obsidian "*/30 * * * *" --sync-anki "0 */6 * * *"   # per system
```

```ini
SCHEDULER__SYNC_CRON=@daily              # every system
SCHEDULER__SYNC_OBSIDIAN_CRON=*/30 * * * *   # only Obsidian
SCHEDULER__SYNC_ANKI_CRON=0 */6 * * *        # only Anki
SCHEDULER__HEALTH_CRON=0 * * * *             # the health check on its own
SCHEDULER__SNAPSHOT_CRON=0 22 * * 0
```

A sync runs the health check afterwards, but a sync that fails or does not run cannot notice itself: the `health` job is what makes
the rules about stale syncs and missed checks (Y2, Y4; see [Event log](events.md#health-rules)) useful.

The five jobs (`sync`, `sync_obsidian`, `sync_anki`, `health`, `snapshot`) are independent and may be combined; they take turns, so two due at once never use the database together. `schedule jobs` shows the plan and the next run of each job without starting anything. Another job is
one more entry in `jobs_of` (`cli/commands/schedule.py`) and one more setting.

## Clearing the database

`db clear` deletes the stored notes, tasks and Anki statistics after asking (`--yes` to skip the
question; `--json` never asks). The vault and the Anki collection are not touched, and `sync` fills the
database again. `--keep-anki` / `--keep-obsidian` limit it to one system.

## Structure

```
episteme/
  settings.py        configuration (was cli.py): what the systems are given
  cli/
    __init__.py      root app: global options, mounts extensions, error handling
    state.py         CliState: global options + the lazily built Episteme
    output.py        emit(): the only way a command prints
    registry.py      Extension, the list of built-ins, entry-point discovery
    commands/        one module per system, each exposing `extension`
  tui/               (proposed, below)
```

Three rules keep it extensible:

1. **A command is thin.** It reads `state_of(ctx).episteme`, calls one method and `emit`s the result.
   Logic belongs in `Episteme` and the systems, where the CLI, the scheduler, a TUI and tests all reach it.
2. **Results are models.** Models already know how to render (`__rich__`) and to dump (`--json`), so
   `emit(state, result)` needs no per-command formatting. A command only builds a table when its
   result is not a model.
3. **A system is an `Extension`.** It brings its command group and its `doctor` checks, and nothing
   else in the CLI changes.

## Adding a system

1. Add the system's module and its settings section to `settings.py`; give `Episteme` a `sync_<name>`.
2. Create `cli/commands/<name>.py`:

    ```python
    app = typer.Typer(help="...", no_args_is_help=True)

    @app.command()
    def stats(ctx: typer.Context) -> None:
        state = state_of(ctx)
        emit(state, state.episteme.notion.statistics)

    def checks(episteme) -> list[CheckResult]: ...

    extension = Extension(name="notion", app=app, help="Notion.", checks=(checks,), order=35)
    ```
3. Add its module to `BUILTIN` in `registry.py`. `sync` takes one more sub-command the same way.

A separate package needs no change to Episteme: declare an entry point,

```toml
[project.entry-points."episteme.cli"]
notion = "episteme_notion.cli:extension"
```

A plug-in that fails to load is logged and skipped.

## TUI

`episteme tui` opens a Textual interface. It is an optional extra: `pip install 'episteme[tui]'`
(`uv sync --extra tui`). Without it the command says so and nothing else is affected.

| Tab | Shows |
|---|---|
| Dashboard | The numbers of `episteme status` (notes, tasks by state, overdue, note types, Anki cards, due, leeches, database), the report of the last sync (`s`) and the checks of `episteme doctor` |
| Obsidian | **Notes** (`F2`): a folder tree of the vault (folders first, with their note counts; opened as you go, so a big vault is fast), a filter (`/`) that narrows it to matching paths or types, and the highlighted note rendered on the right, read from its file. **Tasks** (`F3`): `o` open only, `d` overdue only |
| Anki | `anki_today` and `anki_deck_summary` |
| SQL | An input that runs any reading statement; the views are listed above it |

Keys: `1`-`9` tabs, `s` sync everything, `r` reload, `q` quit.

```
tui/
  app.py        EpistemeApp: tabs, global keys, sync
  registry.py   Screen: how a system adds a tab; built-ins and the `episteme.tui` entry points
  widgets.py    Pane (loads in a worker thread, shows on the UI thread), QueryTable
  tree.py       NoteTree: the vault as a lazily filled folder tree
  screens/      one module per tab, each exposing `screen`
```

It follows the CLI's rules: screens read through `Episteme` and the database views and hold no
logic of their own; a system adds a tab by creating `screens/<name>.py` with a `screen = Screen(...)`
and listing it in `BUILTIN` (or through the `episteme.tui` entry point from another package).
Loading and syncing run in worker threads, taking turns on the one database connection, so the
interface does not block. Not built yet: a scheduler tab, and editing tasks.
