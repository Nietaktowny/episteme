# Azure SQL and SQL Server

Episteme keeps its data in DuckDB by default: one file next to you. It can also keep it in **Azure SQL
Database** or any **SQL Server** (2019 or later), which is what to use when the database should live in the
cloud and be reachable from more than one machine.

Everything else works the same: the sync, the views, the snapshots, the TUI. Only the database changes.

## Setup

Install the extra, which brings the `mssql-python` driver (it needs no ODBC installation) and `pyarrow`
(for snapshots). With uv, the way this project is managed:

```bash
uv sync --extra mssql              # in a checkout of the project
uv add "episteme[mssql]"           # as a dependency of another project
```

or with pip: `pip install "episteme[mssql]"`.

Then point Episteme at the server, in `.env`:

```ini
DATA__ENGINE=mssql
DATA__URL=mssql://episteme_user@myserver.database.windows.net/episteme
DATA__PASSWORD=...
```

The URL is `mssql://user[:password]@host[:port]/database[?options]`. Give the password in `DATA__PASSWORD`, so it
does not need to be percent-encoded and does not end up in the URL (a password in the URL is encoded with `%`
escapes, and it is hidden wherever Episteme shows the URL). Connections are encrypted and the server's
certificate is checked, as Azure SQL requires. For a local server with a self-signed certificate add
`?encrypt=no&trust_server_certificate=yes`.

The tables and views are created by the same migrations as everywhere (`schema_history` table, versions `V1` to `V9`), written in T-SQL. Nothing needs to be
prepared: starting from an empty database, the first connection creates every table, view and function.

## Creating the database

By default the database must exist: Episteme connects to it and creates only its own tables, views and functions in it.
To have Episteme create the database too, if the name in the URL is not there:

```ini
DATA__CREATE_DATABASE=true
DATA__CREATE_OPTIONS=(EDITION = 'GeneralPurpose', SERVICE_OBJECTIVE = 'GP_S_Gen5_1')
```

It does so through the `master` database and waits until the new one is online. That needs a login that may create databases:
on Azure SQL, the server admin or a member of the `dbmanager` role. A login that may not, or cannot see `master`, is not an error:
Episteme logs a warning, goes on and connects, and the database is then reported as missing. That is why this is off by default:
a login limited to one database would warn on every start.

!!! warning "Choose the tier"
    On Azure SQL `CREATE DATABASE` without options creates the server's **default tier**, which may be more
    expensive than you want. Say what you want in `DATA__CREATE_OPTIONS`; the example is a serverless
    General Purpose database with one vCore. Or create the database yourself, in the portal or with `az sql db create`.

## One database, several projects: the schema

A database is a separate store with its own billing; a **schema** is a namespace inside it, a folder for tables.
Episteme keeps everything in a schema of its own, `episteme` unless you choose another name. That is what lets it share a database with other projects:

```ini
DATA__SCHEMA=episteme       # the default
```

Episteme creates the schema if it is missing (the login needs the `CREATE SCHEMA` permission) and puts everything in it:
its tables, views, functions and the `schema_history` table. Every reference is qualified with the schema, so Episteme
never reads or writes a table of the same name in `dbo` or in another project's schema. Two Episteme installs in
one database, each in its own schema, are independent, each with its own migration history.

The default is `episteme`, so by default nothing of Episteme is in `dbo`. Set `DATA__SCHEMA=dbo` to use that one. DuckDB has no schemas and ignores the setting.

Names are letters, digits and underscores (not starting with a digit), and not `sys`, `guest` or `INFORMATION_SCHEMA`.

!!! tip "Querying by hand"
    SQL you type (`episteme db query`, the TUI, SSMS) is not rewritten, so name the schema:
    `SELECT * FROM episteme.vault_notes`. To write plain `vault_notes`, make it the default schema of the database
    user: `ALTER USER episteme_user WITH DEFAULT_SCHEMA = episteme;`. A user per project is the usual way to give each project its own default.

## Serverless databases pause

A serverless Azure SQL database pauses when idle and takes up to about a minute to resume. While it does, connecting
fails with errors such as `40613 Database is not currently available`. Episteme retries those errors, with growing
pauses, for `DATA__CONNECT_TIMEOUT` seconds (default 120) and then gives up saying the database may still be resuming.
A wrong user or password (`18456`) is not retried. Only connecting is retried; a statement that fails is never repeated.

## How it differs from DuckDB

The tables, views and their columns have the same names. What SQL Server cannot say the same way:

| | DuckDB | SQL Server |
|---|---|---|
| A yes/no column (`in_archive`, `is_open`) | `WHERE in_archive` | `WHERE in_archive = 1` |
| A list (`tags`, `topics`, `learn_steps_minutes`) | a list | JSON text; `OPENJSON(tags)` gives its rows |
| `ORDER BY` in a view | the view is sorted | views are not sorted: sort in your query |
| "Today" | the date on your machine | the date in **UTC**, from the function `today()` of the schema |
| Anki "today" | at the rollover hour on your machine | at the rollover hour in **UTC** |
| Path and name comparison | exact | exact (binary collation), so `Note.md` and `note.md` are different notes |
| Views | read-only | a simple view over one table can be written to: Episteme never does, and the SQL console accepts only reading statements |

SQL typed in `episteme db query` and the TUI is T-SQL: `SELECT TOP 10 ...`, not `LIMIT 10`.

!!! note "UTC"
    Azure SQL runs in UTC. `is_overdue` on a task due today therefore turns true when the day changes in
    UTC, which can be one or two hours off from your calendar. All of it comes from one function,
    `today()` (in Episteme's schema), so changing the meaning of "today" later is a one-line migration.

## Snapshots

`episteme snapshot` works on SQL Server too. DuckDB writes Parquet itself; for SQL Server Episteme reads the rows and writes the
file with `pyarrow`, with the same columns and the same order.

## Tests

The tests of this engine run against a real SQL Server in Docker, and skip without one:

```bash
uv sync --extra mssql
uv run pytest tests/test_mssql.py tests/test_engines.py        # starts and removes a container by itself
EPISTEME_TEST_MSSQL_URL='mssql://sa:Password1!@localhost:1433/master' uv run pytest    # or use a server you have
```

The views and the Anki statistics are checked against DuckDB: the same vault and collection are loaded
into both and every view must give the same rows.
