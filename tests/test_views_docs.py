"""docs/views.md: every example runs, shows the output it really produces, and describes a real sample vault."""

import os
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from episteme.settings import DataSettings, ObsidianSettings
from episteme.database import Database
from episteme.obsidian import ObsidianVault

PAGE = Path(__file__).parent.parent / "docs" / "views.md"
EVENT_VIEWS = {"note_events", "dim_note", "fact_events", "fact_sync_runs", "activity_daily",
               "health_findings", "health_events", "fact_violations", "open_alerts",
               "health_detections", "rule_limits", "task_events", "action_events", "alert_deliveries"}   # documented in docs/events.md
TEXT = PAGE.read_text(encoding="utf-8")

# The sample vault of the documentation: path -> (frontmatter or None, modification time or None, body).
# Zero-byte files have no frontmatter and an empty body.
SAMPLE: dict[str, tuple[str | None, str | None, str]] = {
    # Evergreen notes
    "30_Resources/Evergreen/AI/Attention.md": ("type: evergreen\ncreated: 2026-11-10\ndomain: ai\nankified_date: null", "2026-11-12", "A"),
    "30_Resources/Evergreen/AI/Embeddings.md": ("type: evergreen\ncreated: 2026-11-12\ndomain: ai\nankified_date: 2026-11-14", "2026-11-12", "E"),
    "30_Resources/Evergreen/Code/Idempotency.md": ("type: evergreen\ncreated: 2026-11-08\ndomain: code_architecture\nankified_date: null", "2026-11-08", "I"),
    "30_Resources/Evergreen/Misc/Rate limiting.md": ("type: evergreen\ncreated: 2026-11-15\nankified_date: null", "2026-11-15", "R"),
    "30_Resources/Evergreen/AI/Stale.md": ("type: evergreen\ncreated: 2026-09-01\ndomain: ai\nankified_date: 2026-09-05", "2026-11-01", "S"),
    "30_Resources/Evergreen/AI/Current.md": ("type: evergreen\ncreated: 2026-09-01\ndomain: ai\nankified_date: 2026-11-14", "2026-11-01", "C"),
    "30_Resources/Evergreen/Code/Retry budgets.md": (None, None, ""),
    # Lanes and projects
    "20_Lanes/1_Relationships.md": ("type: lane\ncreated: 2026-01-01\nreview_cycle: weekly\nlast_review: 2026-11-01", None, "L"),
    "20_Lanes/2_Competence Enhancement.md": ("type: lane\ncreated: 2026-01-01\nreview_cycle: monthly\nlast_review: 2026-10-01", None, "L"),
    "10_Projects/Episteme.md": ('type: project\nstatus: active\ncreated: 2026-10-01\nlane: "[[2_Competence Enhancement]]"', None,
                                "P\n- [ ] Write the docs 🆔 docs01 ⏫ 📅 2099-01-01\n- [/] Add task views 🆔 views1 📅 2026-11-10\n- [x] Parse tasks 🆔 parse1 ✅ 2026-11-12\n- [ ] A checkbox without an id\n"),
    "10_Projects/Garden.md": ('type: project\nstatus: paused\ncreated: 2026-08-01\nlane: "[[1_Relationships]]"', None,
                              "P\n- [ ] Water the plants 🆔 plant1 📅 2026-10-01\n- [-] Buy a hose 🆔 hose01 ❌ 2026-10-02\n"),
    "10_Projects/Ghost.md": ('type: project\nstatus: active\ncreated: 2026-10-02\nlane: "[[Missing lane]]"', None, "P"),
    "40_Archive/Archived Projects/Finished.md": ('type: project\nstatus: done\ncreated: 2026-05-01\nclosed_date: 2026-11-08\nlane: "[[2_Competence Enhancement]]"', None, "P"),
    "40_Archive/Archived Projects/Old project.md": ("type: project\nstatus: finished\ncreated: 2026-05-01\nupdated: 2026-06-01", None, "P"),
    # Sources
    "30_Resources/Sources/Release It.md": ("type: source\ncreated: 2026-11-09\nkind: book\nauthor: Michael Nygard\nyear: 2018\nconsumed_on: 2026-11-10\ntopics:\n  - resilience\n  - software_architecture", None, "S"),
    "30_Resources/Sources/Fowler on APIs.md": ("type: source\ncreated: 2026-11-09\nkind: article\ntopics: [software_architecture]", None, "S"),
    "30_Resources/Sources/Strange podcast.md": ("type: source\ncreated: 2026-11-09\nkind: podcast", None, "S"),
    # Daily notes: four in the usual folder, one in another
    "_system/00_Inbox/daily_log/2026-11-02.md": ("type: daily_note\ncreated: 2026-11-02", None, "D\n- [ ] Call mom 🆔 call01\n"),
    "_system/00_Inbox/daily_log/2026-11-03.md": ("type: daily_note\ncreated: 2026-11-03", None, "D"),
    "_system/00_Inbox/daily_log/2026-11-04.md": ("type: daily_note\ncreated: 2026-11-04", None, "D"),
    "_system/00_Inbox/daily_log/2026-11-05.md": ("type: daily_note\ncreated: 2026-11-05", None, "D"),
    "00_Inbox/daily_log/2026-10-30.md": ("type: daily_note\ncreated: 2026-10-30", None, "D"),
    # Reviews
    "00_Inbox/weekly_review/weekly_review_2026-10-04.md": ("type: weekly_review\ncreated: 2026-10-04\nstart_range: 2026-09-10\nend_range: 2026-10-04", None, "W"),
    "00_Inbox/weekly_review/weekly_review_2026-11-03.md": ("type: weekly_review\ncreated: 2026-11-03\nstart_range: 2026-10-06\nend_range: 2026-11-03", None, "W"),
    # PKM design
    "30_Resources/Evergreen/Learning/My PKM/Obsidian.md": ("type: pkm_design\nstatus: active\ncreated: 2026-08-01", None, "O"),
    "30_Resources/Evergreen/Learning/My PKM/Properties.md": ('type: pkm_design\nstatus: active\ncreated: 2026-08-16\nparent: "[[Obsidian]]"', None, "P"),
    "30_Resources/Evergreen/Learning/My PKM/Layers.md": ('type: pkm_design\nstatus: draft\ncreated: 2026-08-20\nparent: "[[Properties]]"', None, "L"),
}


def build_sample(root: Path) -> Database:
    for relative, (frontmatter, mtime, body) in SAMPLE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if frontmatter is None else f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
        if mtime is not None:
            stamp = datetime.fromisoformat(mtime).timestamp()
            os.utime(path, (stamp, stamp))
    database = Database(DataSettings(url=":memory:"))
    vault = ObsidianVault(ObsidianSettings(vault_root=root))
    database.sync_notes(vault, vault.scan_for_tasks())
    return database


def cell(value) -> str:
    """How a value is shown in the documentation's output tables."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(cell(item) for item in value) + "]"
    return str(value)


def table(rows: list[dict]) -> list[list[str]]:
    """Header and rows of a query result, as the strings the documentation shows."""
    header = list(rows[0]) if rows else []
    return [header, *([cell(row[column]) for column in header] for row in rows)]


def examples() -> list[tuple[str, list[list[str]] | None]]:
    """Every SQL example of the page with the output table that follows it (None if there is none)."""
    found = []
    for match in re.finditer(r"```sql\n(.*?)```(\n\n((?:\|[^\n]*\n?)+))?", TEXT, flags=re.S):
        sql, _, shown = match.groups()
        parsed = None
        if shown:
            lines = [line for line in shown.strip().splitlines() if not re.fullmatch(r"\|[\s:|-]+\|", line)]
            parsed = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
        found.append((sql, parsed))
    return found


def as_markdown(result: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(row) + " |" for row in result)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("docs") / "vault"
    root.mkdir()
    database = build_sample(root)
    yield database
    database.close()


EXAMPLES = examples()


def test_the_page_has_examples():
    assert len(EXAMPLES) >= 10


@pytest.mark.parametrize("sql", [sql for sql, _ in EXAMPLES], ids=lambda sql: sql.strip().splitlines()[0][:60])
def test_every_example_shows_its_output(database: Database, sql: str):
    shown = dict(EXAMPLES)[sql]
    assert shown is not None, "this example has no output table after it"
    actual = table(database.engine.fetch_all(sql))
    assert shown == actual, "the documented output differs from the real one; it should read:\n" + as_markdown(actual)


def test_every_view_is_listed_on_the_page(database: Database):
    listed = set(re.findall(r"^\| `(\w+)` \|", TEXT, flags=re.M))
    assert listed == {view for view in database.views() if not view.startswith("anki_") and view not in EVENT_VIEWS}  # the Anki views: docs/anki.md, the event views: docs/events.md


def test_every_view_has_an_example(database: Database):
    for view in (view for view in database.views() if not view.startswith("anki_") and view not in EVENT_VIEWS):
        assert any(re.search(rf"\b{view}\b", sql) for sql, _ in EXAMPLES), f"no example uses {view}"


def test_the_page_describes_the_sample_vault():
    """The table on the page lists the sample's notes by type, and must match the sample."""
    section = TEXT.split("## Sample vault", 1)[1].split("\n## ", 1)[0]
    documented = {}
    for line in section.splitlines():
        match = re.match(r"^\| (\S[^|]*?) \| (.+?) \|$", line)
        if match and match.group(1) not in {"Type", "---"} and not set(match.group(1)) <= set("-: "):
            documented[match.group(1)] = {name.strip() for name in match.group(2).split(",")}
    actual: dict[str, set[str]] = {}
    for relative, (frontmatter, _, _) in SAMPLE.items():
        kind = re.search(r"^type: (\w+)", frontmatter or "", flags=re.M)
        actual.setdefault(kind.group(1) if kind else "no frontmatter", set()).add(Path(relative).stem)
    assert documented == actual
