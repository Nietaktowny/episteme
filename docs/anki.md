# Anki

Episteme reads **statistics and scheduling** from a local Anki collection into its database: how every card is doing,
the history of every review, the options of every deck and the tags of every note. It is **read-only**, and it never
reads what a card *says* while syncing: the text of notes, cards and templates, and the descriptions of decks, are not
selected, so they cannot end up in the database. A test guards this by searching everything that is stored for text that
was in the notes. The text of notes can be read [on demand](#reading-the-text-of-notes-on-demand), and is never stored.

!!! info "Why not the content?"
    Obsidian is the home of the notes; Anki is where they are *practised*. What is worth having next to the notes is how
    well the practice is going: which topics (tags) are weak, how much is due, how well things are remembered.

## Setting it up

Point Episteme at the collection (`collection.anki2`) in `.env`:

```bash
ANKI__COLLECTION_PATH=~/.local/share/Anki2/User 1/collection.anki2
ANKI__TIMEZONE=Europe/Warsaw     # optional: which day a review belongs to; the machine's time zone by default
```

Without a path, [`episteme.Episteme.sync_anki`][episteme.Episteme.sync_anki] looks in Anki's data folder (on Linux
`~/.local/share/Anki2`, also inside Flatpak; on macOS and Windows the usual place; `ANKI_BASE` is honoured) and uses
the only profile it finds, or the one named by `ANKI__PROFILE`.

```python
from episteme import Episteme

episteme = Episteme()
print(episteme.sync_anki())      # what was added, updated, left alone and removed in each table
```

`episteme.sync()` includes Anki only when `ANKI__COLLECTION_PATH` is set, so the scheduler can run both with
`scheduler.add_job(episteme.sync, minute="*/15")`.

## How it reads the collection

- **Anki may stay open.** The collection is never read in place. A copy is taken first (with SQLite's backup, which is
  consistent even if Anki is writing); when Anki holds the file locked exclusively, which it does while it runs, the
  file is copied together with its write-ahead log instead, and the copy is read. The original is not written to,
  and nothing is left behind.
- **The review history is read incrementally.** It is large (hundreds of thousands of entries) and only grows, so a
  sync continues after the newest entry it has. If the collection has *older* entries that are new to the database
  (reviews done on a phone and synced later) or entries were removed, the history is read again in full and the result
  says so (`reviews_reloaded`).
- **Everything else is compared as data**, like the notes of the vault: new rows are added, changed ones rewritten,
  identical ones left alone, and rows that are gone from Anki are removed, all in one transaction.
- **Schema 18 only** (Anki 2.1.50, from 2021, and later). An older collection is refused with a message; opening it
  once in a recent Anki upgrades it.

## What is stored

| Table | One row per | Holds |
|---|---|---|
| `anki_collection` | the collection | schema and scheduler version, creation day, rollover hour, whether FSRS is on, when it was synced |
| `anki_decks` | deck | name (`Parent::Child`), parent, filtered or not, its options preset, its own limits |
| `anki_deck_options` | options preset | limits, steps, intervals, leech rules, orders, burying, desired retention and FSRS parameters |
| `anki_notes` | note | its type and its tags, no fields |
| `anki_cards` | card | state, due, interval, ease, lapses, suspended and buried, the FSRS memory state |
| `anki_reviews` | review history entry | when, which card, rating, kind, intervals, time spent |

**Times are UTC and days are Anki days.** Anki's day starts at the *rollover hour* (4 a.m. by default), not at midnight:
a review at 03:30 still belongs to the day before. `due_date` and `review_day` are Anki days, in the configured time
zone. A card in a filtered deck is counted in the deck it belongs to.

## Views

All the views start with `anki_`, are read-only and carry no card content. Use `anki_today` rather than
`current_date` for "today".

| View | Contents |
|---|---|
| `anki_today` | The current Anki day. |
| `anki_card_details` | Every card with `deck_name`, `note_type_name`, `tags`, `maturity` (new, young, mature: 21 days or more), `is_due`, `days_overdue`, `is_leech` and the FSRS `retrievability`. |
| `anki_note_tags` | One row per note and tag. |
| `anki_due_forecast` | Cards due per day; those already overdue are due today. |
| `anki_deck_summary` | Per deck: cards by state and maturity, suspended, buried, due, overdue, leeches, lapses, average interval, stability and difficulty. |
| `anki_deck_settings` | Per deck, the scheduling options that apply: its preset, limits (its own if it overrides the preset's), steps, leech rules, order, desired retention. |
| `anki_daily_reviews` | Per day: answers by kind and rating, minutes spent, retention. |
| `anki_streaks` | Runs of consecutive days with at least one answer. |
| `anki_deck_retention` | Per deck: how many reviews were answered and how many were not *Again*, over all time and the last 30 days. |
| `anki_leeches` | Cards tagged `leech`, the most lapsed first. |
| `anki_tag_summary` | Per tag: notes, cards, new, mature, due, leeches, lapses, average stability. |

## Examples

The outputs below are real: they come from the sample collection of this page's test. It has decks `Languages` and
`Languages::Polish` (which has its own limit of 3 new cards a day and uses the stricter `Strict` preset),
`Programming`, `Empty` and a filtered deck, with nine cards and nine history entries in March 2023.

How the decks are doing:

```sql
SELECT deck_name, cards, new, learning, young, mature, suspended, buried
FROM anki_deck_summary
WHERE cards > 0
```

| deck_name         | cards | new | learning | young | mature | suspended | buried |
|-------------------|-------|-----|----------|-------|--------|-----------|--------|
| Languages         | 2     | 0   | 1        | 0     | 1      | 0         | 0      |
| Languages::Polish | 2     | 0   | 0        | 1     | 1      | 0         | 0      |
| Programming       | 5     | 2   | 1        | 1     | 1      | 1         | 1      |

The scheduling options that apply to each deck. A deck's own limit wins over its preset's:

```sql
SELECT deck_name, options_name, new_per_day, reviews_per_day, has_own_limits, desired_retention
FROM anki_deck_settings
WHERE deck_name <> 'Default'
```

| deck_name         | options_name | new_per_day | reviews_per_day | has_own_limits | desired_retention |
|-------------------|--------------|-------------|-----------------|----------------|-------------------|
| Empty             | Default      | 20          | 200             | false          | 0.9               |
| Languages         | Default      | 20          | 200             | false          | 0.9               |
| Languages::Polish | Strict       | 3           | 50              | true           | 0.8               |
| Programming       | Default      | 20          | 30              | true           | 0.9               |

What is due, day by day. Cards that are already overdue are due *today*, and today changes, so this leaves them out:

```sql
SELECT day, cards, reviews, learning
FROM anki_due_forecast
WHERE overdue = 0 AND day < DATE '2090-01-01'
```

| day        | cards | reviews | learning |
|------------|-------|---------|----------|
| 2076-07-22 | 1     | 1       | 0        |
| 2076-07-23 | 1     | 0       | 1        |
| 2076-07-24 | 1     | 1       | 0        |

What was practised, and how well. A review at 03:30 on 10 March belongs to the 9th, and a manual change of a card is not
an answer, so the 13th is not listed:

```sql
SELECT day, answers, again, good, minutes, retention
FROM anki_daily_reviews
```

| day        | answers | again | good | minutes | retention |
|------------|---------|-------|------|---------|-----------|
| 2023-03-09 | 2       | 1     | 1    | 0.2     | 0.5       |
| 2023-03-10 | 1       | 0     | 1    | 0.1     | NULL      |
| 2023-03-11 | 1       | 0     | 0    | 0.1     | 1.0       |
| 2023-03-12 | 2       | 0     | 1    | 0.2     | 1.0       |
| 2023-03-15 | 2       | 1     | 1    | 0.2     | 0.5       |

How long the habit lasted:

```sql
SELECT start_day, end_day, days, is_current
FROM anki_streaks
```

| start_day  | end_day    | days | is_current |
|------------|------------|------|------------|
| 2023-03-09 | 2023-03-12 | 4    | false      |
| 2023-03-15 | 2023-03-15 | 1    | false      |

Retention per deck. The learning steps and the review of a card that was deleted are not counted:

```sql
SELECT deck_name, reviews, retention
FROM anki_deck_retention
```

| deck_name         | reviews | retention |
|-------------------|---------|-----------|
| Languages         | 2       | 0.5       |
| Languages::Polish | 3       | 1.0       |

Which cards are due and which are leeches. `is_due` is false for a buried or suspended card, and a leech is a card of a
note that Anki tagged `leech`:

```sql
SELECT card_id, deck_name, maturity, is_due, is_leech
FROM anki_card_details
ORDER BY card_id
```

| card_id | deck_name         | maturity | is_due | is_leech |
|---------|-------------------|----------|--------|----------|
| 1001    | Languages         | mature   | true   | true     |
| 1002    | Languages::Polish | mature   | false  | false    |
| 1003    | Programming       | new      | false  | false    |
| 1004    | Programming       | new      | false  | false    |
| 1005    | Programming       | young    | false  | false    |
| 1006    | Languages         | young    | false  | true     |
| 1007    | Languages::Polish | young    | false  | false    |
| 1008    | Programming       | mature   | false  | false    |
| 1009    | Programming       | young    | true   | false    |

```sql
SELECT card_id, deck_name, lapses, is_suspended
FROM anki_leeches
```

| card_id | deck_name | lapses | is_suspended |
|---------|-----------|--------|--------------|
| 1001    | Languages | 9      | false        |
| 1006    | Languages | 1      | false        |

The weak and strong topics, by tag:

```sql
SELECT tag, notes, cards, mature, leeches, lapses
FROM anki_tag_summary
ORDER BY lapses DESC, tag
```

| tag     | notes | cards | mature | leeches | lapses |
|---------|-------|-------|--------|---------|--------|
| leech   | 1     | 2     | 1      | 2       | 10     |
| vocab   | 2     | 4     | 2      | 2       | 10     |
| code    | 2     | 3     | 0      | 0       | 0      |
| grammar | 1     | 2     | 1      | 0       | 0      |
| python  | 1     | 2     | 0      | 0       | 0      |

```sql
SELECT count(*) AS tagged_notes, count(DISTINCT tag) AS tags
FROM anki_note_tags
```

| tagged_notes | tags |
|--------------|------|
| 7            | 5    |

`anki_today` is the date counted from the rollover hour (4 a.m. in the sample), so for the first hours after midnight it is still yesterday:

```sql
SELECT today = (current_localtimestamp() - INTERVAL 4 HOUR)::DATE AS is_the_anki_day
FROM anki_today
```

| is_the_anki_day |
|-----------------|
| true            |

## Reading the text of notes on demand

Sometimes the statistics point at something and you want to *see* it: which cards are the leeches, say. For that the
text of notes can be read, but only if you switch it on, only when you ask, and only for the notes you ask for:

```bash
ANKI__ALLOW_CONTENT=true
```

```python
leeches = episteme.data.engine.fetch_all("SELECT DISTINCT note_id FROM anki_leeches")
for note in episteme.anki.note_contents(row["note_id"] for row in leeches):
    print(note.note_id, note.tags, note.fields)       # {"Front": "...", "Back": "..."}
```

- **Lazy.** `note_contents` is a generator: nothing is read until it is iterated, then a copy of the collection is
  taken and the notes are yielded in batches. `note_content(note_id)` reads one note.
- **Off by default.** Without `ANKI__ALLOW_CONTENT` it raises `AnkiContentDisabledError`.
- **Never stored.** The text goes to the caller and nowhere else: the sync does not use it (a test makes sure of that),
  and no table has a column for it.

## What is not stored

- The **content**: fields of notes, text of cards, templates, deck descriptions, media.
- The **study counts of today** and the other counters Anki keeps for its own screens.
- Nothing is left out of the **review history**, including entries a preset tells FSRS to ignore
  (`anki_deck_options.ignore_revlogs_before`).
- The ease factor of a card is stored (`ease_percent`) but with FSRS on the scheduler does not use it; use the FSRS
  columns (`stability_days`, `difficulty`, `retrievability`).
