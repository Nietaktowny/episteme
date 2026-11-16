# Templates

A note can say which **template** it was created from, and which **version** of it. Episteme fills
`MarkdownNote.template` and `MarkdownNote.template_version`, and stores them in the `template` and
`template_version` columns of the `notes` table.

## Where the values come from

The notes are the source, not the code that created them:

- **`template_version`** is the `template_version` property of the note's frontmatter, which a managed
  template writes into every note it creates (it is a required property of the PKM's registry). `1.3`, `2`
  and `"1.1"` are versions; anything else (`"1.1.0"`, `x`) is not, and gives `None`. A note without the
  property was not created from a template and has neither a version nor a template.
- **`template`** is **not recorded in the notes**, so it is derived: the template of a note of `type: evergreen`
  is `evergreen_template`, if the templates folder (`99_Templates`) has a template of that name in any
  version (`v1.0_evergreen_template.md`, `v1.3_evergreen_template.md`). If it has none, the note has a version
  but no template name.

The notes of the templates folder are not notes created from a template, so they have neither.

## Types whose template has another name

For a type whose template is not called `<type>_template`, name it in the settings:

```python
ObsidianSettings(template_by_type={"meeting": "meeting_input_template", "weekly_status": "work_weekly_status_template"})
```

(or `OBSIDIAN__TEMPLATE_BY_TYPE='{"meeting": "meeting_input_template"}'` in the environment).

## When it happens

Mapping the vault stays lazy and does not read the notes, so the fields are empty until they are filled:

- **`episteme.sync_obsidian()`** (and `sync()`) fills them for every note before syncing, so the database always
  has them. It reads the frontmatter anyway, to store it.
- **`ObsidianVault.detect_templates()`** does it on its own and returns how many notes were created from a
  template. A note that cannot be read is logged and skipped.
- **`ObsidianVault.create_from_template()`** sets them on the note it creates, from what it used.
- `MarkdownNote.detect_template(template_names, template_by_type)` does it for one note.

## Querying

```sql
SELECT template, template_version, count(*) AS notes
FROM notes
WHERE template_version IS NOT NULL
GROUP BY template, template_version
ORDER BY template, template_version;
```

The note views (`vault_notes` and the typed ones) do not expose the two columns.

## Caveats

- The version is a number (`float`), as in the template file names (`v1.3_...`). `1.10` and `1.1` are the same
  number; the registry uses one decimal place.
- A name guessed from `type` can be wrong if a type has a differently named template and the setting above
  does not say so. It is `None`, not a wrong name, when no template of that name exists.
