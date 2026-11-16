# Snapshots

A **snapshot** is the state of the vault's notes at one moment, kept in a Parquet file. A later
snapshot, or the live database, can be compared with it to see what changed in between: which notes
are new, which are gone, which were moved, which texts were edited.

The database cannot do this alone. It mirrors the vault as of the last sync and may even live in memory,
so the history has to be stored somewhere that outlives it. A snapshot is a plain file inside the vault,
so it is kept and synchronized with the vault.

## Taking a snapshot

```python
snapshot = episteme.snapshot()               # the label defaults to today's date
snapshot = episteme.snapshot("2026-11-20")   # or any label, e.g. the date of a review
snapshot.path, snapshot.notes                # the file, and how many notes it holds
```

`Episteme.snapshot` syncs the vault into the database first, so the snapshot shows the vault as it is now,
and writes `<vault>/_system/snapshots/notes_<label>.parquet`. The folder is the `snapshots_dir`
setting of the vault (`OBSIDIAN__SNAPSHOTS_DIR`), relative to the vault root.

A snapshot is kept: taking one with a label that exists raises `FileExistsError` instead of replacing it,
unless `overwrite=True` is passed. The file is written completely or not at all. The label may contain
letters, digits, `.`, `_` and `-`.

`Database.snapshot(path)` is the lower-level operation. It writes the database as it is, without syncing,
to any path.

## What a snapshot holds

One row per note:

| Column | Contents |
|---|---|
| `path` | Path relative to the vault. |
| `name` | Note name. |
| `in_archive` | Whether the note is in the archive. |
| `is_empty` | Whether the file is zero bytes. |
| `size` | Size of the file in bytes. |
| `type` | The `type` property, or `NULL`. |
| `created` | The `created` property as a date, or `NULL`. |
| `status` | The `status` property, or `NULL`. |
| `body_hash` | SHA-256 of the text after the frontmatter. |
| `frontmatter` | The whole frontmatter as JSON text. |

A snapshot contains nothing that depends on the machine: no file times and no absolute paths. The same
vault gives the same snapshot on every machine.

`body_hash` is what tells an edit of the text from a change of properties: it changes when the text
changes, even if the size stays the same, and it does not change when only the frontmatter does.

## Reading a snapshot

A snapshot is an ordinary Parquet file, which DuckDB reads directly:

```text
SELECT * FROM read_parquet('<vault>/_system/snapshots/notes_2026-11-03.parquet');
SELECT * FROM read_parquet('<vault>/_system/snapshots/*.parquet', filename = true);   -- all of them
```

## Comparing with the live database

The examples below use the sample vault of the [views page](views.md). A snapshot of it was taken
(`notes_2026-11-03.parquet`) and then the vault changed:

- `10_Projects/Episteme.md` was moved to `40_Archive/Archived Projects/`,
- the text of `Attention` was edited without changing its length,
- `Embeddings` got a new `ankified_date`, which changes its properties only,
- `Rate limiting` was deleted,
- an empty placeholder, `Circuit breakers`, was added.

The vault was then synced. In the queries, `previous` is the snapshot:

```text
CREATE VIEW previous AS
SELECT * FROM read_parquet('<vault>/_system/snapshots/notes_2026-11-03.parquet');
```

Notes that were added or removed:

```sql
SELECT 'added' AS change, n.path
FROM vault_notes n ANTI JOIN previous USING (path)
UNION ALL
SELECT 'removed', p.path
FROM previous p ANTI JOIN vault_notes USING (path)
ORDER BY 1, 2;
```

| change | path |
|---|---|
| added | 30_Resources/Evergreen/Code/Circuit breakers.md |
| added | 40_Archive/Archived Projects/Episteme.md |
| removed | 10_Projects/Episteme.md |
| removed | 30_Resources/Evergreen/Misc/Rate limiting.md |

Notes that were moved or renamed. A note has no identifier, so it is recognized by what did not change:
its text, its type and its creation date:

```sql
SELECT p.path AS before, n.path AS after
FROM (SELECT * FROM previous ANTI JOIN vault_notes USING (path)) p
JOIN (SELECT * FROM vault_notes ANTI JOIN previous USING (path)) n
  ON p.body_hash = n.body_hash
 AND p.type IS NOT DISTINCT FROM n.type
 AND p.created IS NOT DISTINCT FROM n.created;
```

| before | after |
|---|---|
| 10_Projects/Episteme.md | 40_Archive/Archived Projects/Episteme.md |

Notes whose text was edited. `same_size` shows that the size alone would not have revealed the edit:

```sql
SELECT n.name, n.size = p.size AS same_size
FROM vault_notes n JOIN previous p USING (path)
WHERE n.body_hash <> p.body_hash;
```

| name | same_size |
|---|---|
| Attention | true |

Notes whose properties changed but whose text did not:

```sql
SELECT n.name
FROM vault_notes n JOIN previous p USING (path)
WHERE n.frontmatter::VARCHAR <> p.frontmatter AND n.body_hash = p.body_hash;
```

| name |
|---|
| Embeddings |

The number of empty placeholders, then and now:

```sql
SELECT
    (SELECT count(*) FROM previous WHERE is_empty) AS before,
    (SELECT count(*) FROM vault_notes WHERE is_empty) AS now;
```

| before | now |
|---|---|
| 1 | 2 |

Empty placeholders that appeared since the snapshot:

```sql
SELECT n.name
FROM vault_notes n ANTI JOIN previous USING (path)
WHERE n.is_empty;
```

| name |
|---|
| Circuit breakers |

## Limits

- **A moved note is recognized, not identified.** The match on text, type and creation date fails for
  notes without a `created` and is ambiguous for notes with identical text. In particular every empty note
  has the same hash, so deleting one placeholder and adding another looks like a move.
- **`body_hash` covers the text only.** Changes of properties are found by comparing `frontmatter`.
- **`Database.snapshot` shows the database, not the vault.** It is as recent as the last sync.
  `Episteme.snapshot` syncs first.
- **A snapshot is not human-readable**, unlike the review's YAML file. Read it with SQL.
