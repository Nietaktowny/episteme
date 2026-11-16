# Database views

The `notes` table stores every note of the vault together with its whole frontmatter as JSON. The
**views** decode the vault's conventions (the property registry in `Properties.md`) once, so that
queries do not have to repeat them: `SELECT * FROM evergreen_notes` replaces
`WHERE frontmatter ->> '$.type' = 'evergreen'`.

The views are created by migrations (`database/sql/duckdb/migrations/V3__create_note_views.sql` and
`V4__create_analysis_views.sql`), are read-only, and each one carries a description that the
database itself can list:

```python
episteme.data.views()        # {"evergreen_notes": "Notes of type evergreen, ...", ...}
```

## Conventions

- **`type` decides what a note is, not its folder.** Three things refer to folders: the archive
  (`40_Archive/`), the zero-byte Evergreen placeholders (`30_Resources/Evergreen/`, which have no
  frontmatter and therefore no type) and the `folder` column itself.
- **`created` is the semantic creation date**, taken from the frontmatter. The file's modification
  time is exposed as `modified_at`. The file's creation time is deliberately left out, because it
  differs between machines.
- **Paths are relative to the vault**, as everywhere in the database.
- **Links are decoded.** `lane: "[[2_Competence Enhancement]]"` becomes
  `lane = '2_Competence Enhancement'`. The macro `link_target()` decodes any link the same way.
- **Malformed values become `NULL`** and never make a view fail. `property_issues` reports them.
- Every view has `path` and `name`. The typed views also have all columns of `vault_notes`.

## Views

| View | Contents |
|---|---|
| `vault_notes` | Every note: `type`, `created`, `status`, `folder`, `in_archive`, `is_empty`, `modified_at`, `body_hash`, `frontmatter`. |
| `evergreen_notes` | `domain`, `ankified_date` (`NULL` means not yet processed into Anki). |
| `evergreen_placeholders` | Zero-byte notes in the Evergreen folder: the knowledge-gap TODOs. |
| `project_notes` | `lane`, `closed_date`, `is_open` (active or paused), `is_closed` (done or abandoned). |
| `source_notes` | `kind`, `author`, `organization`, `year`, `url`, `consumed_on`, `is_consumed`, `topics` (a list). |
| `source_topics` | One row per source and topic. |
| `daily_notes` | `day`, parsed from the note name (`NULL` if the name is not `YYYY-MM-DD`). |
| `weekly_reviews` | `start_range`, `end_range`, `days`, `previous_end_range`, `uncovered_days`, `is_latest`. |
| `review_schedule` | Notes with a `review_cycle`: `last_review`, `days_since_review`, `never_reviewed`, `is_overdue`. |
| `evergreen_anki_state` | `anki_state`: `never`, `stale` or `current`. |
| `property_usage` | Every frontmatter property with the number of notes that use it, per type. |
| `property_issues` | Violations of the registry, one row per issue (`rule`, `detail`). |
| `pkm_design_tree` | The `pkm_design` notes as a tree: `parent`, `depth`, `trail`. |

## Semantics of individual columns

- `uncovered_days` is `0` when a review starts the day after the previous one ended, positive when
  days were skipped between them and negative when the reviews overlap. The first review has no
  predecessor, so its value is `NULL`.
- `is_overdue` is a hint, not a verdict. A review cycle expresses an intention, not an SLA; the
  lengths used are 7 days for `weekly` and 31 days for `monthly`. It is `NULL` for notes that were
  never reviewed (see `never_reviewed`).
- `anki_state = 'stale'` means the file was modified after `ankified_date`. The comparison uses the
  file's modification time, so it can over-report after files were synchronized between machines.
- `property_issues` also lists archived notes, marked with `in_archive = TRUE`. The registry does not
  require rewriting archived notes, so queries about the active vault filter with `NOT in_archive`.
- A value that is not what its property requires (a `topics` that is not a list, a `year` that is not
  a number) reads as `NULL` in the typed views and appears in `property_issues`.

## Sample vault

The examples below were run against a small sample vault. Its notes, by type:

| Type | Notes |
|---|---|
| evergreen | Attention, Current, Embeddings, Idempotency, Rate limiting, Stale |
| project | Episteme, Finished, Garden, Ghost, Old project |
| lane | 1_Relationships, 2_Competence Enhancement |
| source | Fowler on APIs, Release It, Strange podcast |
| daily_note | 2026-10-30, 2026-11-02, 2026-11-03, 2026-11-04, 2026-11-05 |
| weekly_review | weekly_review_2026-10-04, weekly_review_2026-11-03 |
| pkm_design | Layers, Obsidian, Properties |
| no frontmatter | Retry budgets |

A few details matter for the outputs:

- `Ghost` links to a lane that does not exist, and `Strange podcast` has a source `kind` that is not
  in the registry. They are the only problems in the active vault.
- `Old project` is archived and still uses a deprecated property and an unregistered status; as an
  archived note it is excluded wherever the examples filter `NOT in_archive`.
- Four daily notes are stored in `_system/00_Inbox/daily_log`, one in `00_Inbox/daily_log`.
- The latest review ends on 2026-11-03; the one before it ended on 2026-10-04, so one day (2026-10-05)
  is not covered by any review.

In the output tables, `NULL` means "no value", lists are shown in brackets and dates as `YYYY-MM-DD`.

## Examples

How many notes of each type there are. Notes without frontmatter have no type:

```sql
SELECT type, count(*) AS notes
FROM vault_notes
GROUP BY type
ORDER BY type NULLS LAST;
```

| type | notes |
|---|---|
| daily_note | 5 |
| evergreen | 6 |
| lane | 2 |
| pkm_design | 3 |
| project | 5 |
| source | 3 |
| weekly_review | 2 |
| NULL | 1 |

Evergreen notes created in a period, by domain. Notes without a domain are left out of the table but still count towards the total of the period:

```sql
SELECT domain, count(*) AS created
FROM evergreen_notes
WHERE created BETWEEN DATE '2026-11-04' AND DATE '2026-11-20' AND domain IS NOT NULL
GROUP BY domain
ORDER BY domain;
```

| domain | created |
|---|---|
| ai | 2 |
| code_architecture | 1 |

The knowledge-gap placeholders: zero-byte notes in the Evergreen folder.

```sql
SELECT name, folder
FROM evergreen_placeholders;
```

| name | folder |
|---|---|
| Retry budgets | 30_Resources/Evergreen/Code |

Evergreen notes that need another pass in Anki:

```sql
SELECT name, anki_state
FROM evergreen_anki_state
WHERE anki_state <> 'current' AND NOT in_archive
ORDER BY name;
```

| name | anki_state |
|---|---|
| Attention | never |
| Idempotency | never |
| Rate limiting | never |
| Stale | stale |

Active and paused projects:

```sql
SELECT status, list(name ORDER BY name) AS projects
FROM project_notes
WHERE is_open
GROUP BY status
ORDER BY status;
```

| status | projects |
|---|---|
| active | [Episteme, Ghost] |
| paused | [Garden] |

Sources with their properties. `topics` is a list:

```sql
SELECT name, kind, year, is_consumed, topics
FROM source_notes
ORDER BY name;
```

| name | kind | year | is_consumed | topics |
|---|---|---|---|---|
| Fowler on APIs | article | NULL | false | [software_architecture] |
| Release It | book | 2018 | true | [resilience, software_architecture] |
| Strange podcast | podcast | NULL | false | NULL |

How many sources cover each topic:

```sql
SELECT topic, count(*) AS sources
FROM source_topics
GROUP BY topic
ORDER BY topic;
```

| topic | sources |
|---|---|
| resilience | 1 |
| software_architecture | 2 |

Where daily notes are stored. More than one row means that daily notes exist in an alternate folder:

```sql
SELECT folder, count(*) AS notes, min(day) AS first_day, max(day) AS last_day
FROM daily_notes
GROUP BY folder
ORDER BY folder;
```

| folder | notes | first_day | last_day |
|---|---|---|---|
| 00_Inbox/daily_log | 1 | 2026-10-30 | 2026-10-30 |
| _system/00_Inbox/daily_log | 4 | 2026-11-02 | 2026-11-05 |

The chain of reviews. `uncovered_days` counts the days between a review and the one before it that no review covers; `0` means they are contiguous:

```sql
SELECT name, start_range, end_range, uncovered_days, is_latest
FROM weekly_reviews
ORDER BY end_range;
```

| name | start_range | end_range | uncovered_days | is_latest |
|---|---|---|---|---|
| weekly_review_2026-10-04 | 2026-09-10 | 2026-10-04 | NULL | false |
| weekly_review_2026-11-03 | 2026-10-06 | 2026-11-03 | 1 | true |

The daily notes that follow the latest review:

```sql
SELECT d.name, d.day
FROM weekly_reviews r, daily_notes d
WHERE r.is_latest AND d.day > r.end_range
ORDER BY d.day;
```

| name | day |
|---|---|
| 2026-11-04 | 2026-11-04 |
| 2026-11-05 | 2026-11-05 |

Notes that are reviewed on a schedule. `days_since_review` and `is_overdue` are computed against the current date, so they are not shown here:

```sql
SELECT name, review_cycle, last_review, never_reviewed
FROM review_schedule
ORDER BY name;
```

| name | review_cycle | last_review | never_reviewed |
|---|---|---|---|
| 1_Relationships | weekly | 2026-11-01 | false |
| 2_Competence Enhancement | monthly | 2026-10-01 | false |

The PKM design notes as a tree:

```sql
SELECT depth, trail
FROM pkm_design_tree
ORDER BY trail;
```

| depth | trail |
|---|---|
| 0 | Obsidian |
| 1 | Obsidian > Properties |
| 2 | Obsidian > Properties > Layers |

Which properties projects use, and how many projects use each. A rarely used property is often a typo or a leftover:

```sql
SELECT key, notes
FROM property_usage
WHERE type = 'project'
ORDER BY key;
```

| key | notes |
|---|---|
| closed_date | 1 |
| created | 5 |
| lane | 4 |
| status | 5 |
| type | 5 |
| updated | 1 |

Problems in the active vault, by rule:

```sql
SELECT rule, count(*) AS notes
FROM property_issues
WHERE NOT in_archive
GROUP BY rule
ORDER BY notes DESC, rule;
```

| rule | notes |
|---|---|
| invalid_kind | 1 |
| unresolved_lane | 1 |

## Changing the views

A change to a view is a new versioned migration that uses `CREATE OR REPLACE VIEW` and describes the
view with `COMMENT ON VIEW` right after it, because replacing a view drops its comment. Views are
bound when they are created, so a migration that changes the `notes` table must also recreate the
views that read it.

`tests/test_views.py` checks that every view binds, is described and returns the expected rows, and
`tests/test_views_docs.py` checks that every example on this page shows the output it really produces on the
sample vault, that the sample vault is described accurately and that every view is listed and has an example.
