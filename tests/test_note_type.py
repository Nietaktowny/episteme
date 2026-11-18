"""``type`` and ``created`` are columns of the notes: what a note writes wins, its folder fills the gaps.

The behaviour of the model and the vault needs no database; what the database stores, and the upgrade of a database
that already holds notes, is tested on both engines (the SQL Server ones skip without a server).
"""

import re
import shutil
from datetime import date
from pathlib import Path, PurePosixPath

import pytest

from test_events import LATEST_SCHEMA
from episteme.database import Database
from episteme.database._duckdb import DuckDBEngine
from episteme.database.migrations import Migrations
from episteme.database.queries import QueryStore
from episteme.models.obsidian import MarkdownNote
from episteme.obsidian import ObsidianVault
from episteme.settings import DEFAULT_TYPE_BY_FOLDER, DataSettings, ObsidianSettings


def write(root: Path, relative: str, frontmatter: str | None = None, body: str = "Body") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if frontmatter is None else f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def vault_of(root: Path, **settings) -> ObsidianVault:
    return ObsidianVault(ObsidianSettings(vault_root=root, **settings))


def detached(frontmatter: dict, inferred: str | None = None) -> MarkdownNote:
    note = MarkdownNote(name="n", path="n.md", inferred_type=inferred)
    note.__dict__["frontmatter"] = frontmatter
    return note


class TestTheTypeOfANote:
    def test_a_written_type_is_the_type(self):
        assert detached({"type": "project"}).type == "project"

    def test_a_written_type_wins_over_the_folder(self):
        assert detached({"type": "pkm_design"}, inferred="evergreen").type == "pkm_design"

    def test_the_folder_fills_in_for_a_note_that_writes_none(self):
        assert detached({}, inferred="evergreen").type == "evergreen"
        assert detached({"status": "x"}, inferred="lane").type == "lane"

    def test_no_type_at_all_is_none(self):
        assert detached({}).type is None

    @pytest.mark.parametrize("written", ["", "   ", None, ["a", "b"], 7, {"a": 1}, True])
    def test_a_type_that_is_not_a_usable_text_does_not_count(self, written):
        assert detached({"type": written}, inferred="source").type == "source"

    def test_surrounding_spaces_are_dropped(self):
        assert detached({"type": "  lane "}).type == "lane"


class TestTemplates:
    """A template writes the type of the notes made from it (`type: evergreen`), but it is a template."""

    def test_the_folder_beats_what_the_note_writes(self):
        note = detached({"type": "evergreen"}, inferred="project")
        note.forced_type = "template"
        assert note.type == "template"

    def test_a_note_without_a_forcing_folder_is_not_affected(self):
        assert detached({"type": "evergreen"}, inferred="project").type == "evergreen"

    @pytest.fixture
    def vault(self, tmp_path: Path) -> ObsidianVault:
        write(tmp_path, "99_Templates/v1.0_evergreen_template.md", "type: evergreen\ntemplate_version: 1.0")
        write(tmp_path, "99_Templates/Sub/v1.0_project_template.md", "type: project")
        write(tmp_path, "40_Archive/Archived Templates/v0.9_lane_template.md", "type: lane")
        write(tmp_path, "40_Archive/Archived Templates/no_frontmatter.md", None, body="x")
        write(tmp_path, "40_Archive/Archived Projects/Old.md", "type: project")
        write(tmp_path, "30_Resources/Evergreen/AI/Note.md", "type: evergreen\ntemplate_version: 1.0")
        return vault_of(tmp_path)

    def test_templates_are_templates_in_both_folders_and_below(self, vault: ObsidianVault):
        got = {note.name: note.type for note in vault}
        assert got == {
            "v1.0_evergreen_template": "template", "v1.0_project_template": "template",
            "v0.9_lane_template": "template", "no_frontmatter": "template",
            "Old": "project", "Note": "evergreen",
        }

    def test_the_template_of_a_note_type_is_still_found_by_what_it_writes(self, vault: ObsidianVault):
        """Detecting which template a note follows goes by the type the note writes, not by the templates' own type."""
        vault.detect_templates()
        assert vault["Note"].template == "evergreen_template"
        assert vault["v1.0_evergreen_template"].type == "template" and vault["Note"].type == "evergreen"

    def test_rules_are_settings(self, tmp_path: Path):
        write(tmp_path, "Blueprints/b.md", "type: project")
        assert vault_of(tmp_path, forced_type_by_folder={"Blueprints": "template"})["b"].type == "template"
        assert vault_of(tmp_path, forced_type_by_folder={})["b"].type == "project"

    def test_the_longest_forcing_folder_wins(self, tmp_path: Path):
        write(tmp_path, "T/x.md", "type: project")
        write(tmp_path, "T/Inner/y.md", "type: project")
        vault = vault_of(tmp_path, forced_type_by_folder={"T": "template", "T/Inner": "snippet"})
        assert (vault["x"].type, vault["y"].type) == ("template", "snippet")

    def test_the_defaults(self):
        from episteme.settings import DEFAULT_FORCED_TYPE_BY_FOLDER

        assert DEFAULT_FORCED_TYPE_BY_FOLDER == {"99_Templates": "template", "40_Archive/Archived Templates": "template"}
        assert ObsidianSettings().forced_type_by_folder == DEFAULT_FORCED_TYPE_BY_FOLDER

    def test_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        from episteme.settings import EpistemeSettings

        monkeypatch.setenv("OBSIDIAN__FORCED_TYPE_BY_FOLDER", '{"Blueprints": "template"}')
        assert EpistemeSettings(_env_file=None).obsidian.forced_type_by_folder == {"Blueprints": "template"}

    @pytest.mark.parametrize("rules", [{"../x": "y"}, {"": "x"}, {"a": ""}])
    def test_bad_rules_are_refused(self, rules: dict):
        with pytest.raises(ValueError):
            ObsidianSettings(forced_type_by_folder=rules)

    def test_an_attached_note_gets_the_forced_type_too(self, tmp_path: Path):
        loaded = MarkdownNote(name="t", path="99_Templates/t.md")
        assert vault_of(tmp_path).attach(loaded).forced_type == "template"


class TestTheCreationDate:
    @pytest.mark.parametrize("written, expected", [
        (date(2026, 11, 10), date(2026, 11, 10)),            # YAML: created: 2026-11-10
        ("2026-11-10", date(2026, 11, 10)),                  # created: "2026-11-10"
        ("2026-11-10T08:30:00", date(2026, 11, 10)),
        ("2026-11-10 08:30", date(2026, 11, 10)),
        ("soon", None), ("", None), (None, None), (["2026-11-10"], None), (20261110, None), ("2026-13-45", None),
    ])
    def test_values(self, written, expected):
        assert detached({"created": written}).created == expected

    def test_a_note_without_the_property_has_none(self):
        assert detached({}).created is None

    def test_the_files_time_is_not_a_fallback(self, tmp_path: Path):
        write(tmp_path, "a.md", "status: x")
        note = vault_of(tmp_path)["a"]
        assert note.created is None and note.created_at is not None


class TestFolderRules:
    @pytest.fixture
    def vault(self, tmp_path: Path) -> ObsidianVault:
        return vault_of(tmp_path, type_by_folder={"a": "x", "a/b": "y", "Ünï/c": "z"})

    @pytest.mark.parametrize("path, expected", [
        ("a/n.md", "x"), ("a/b/n.md", "y"), ("a/b/c/n.md", "y"), ("a/q/n.md", "x"),
        ("ab/n.md", None), ("a.md", None), ("n.md", None), ("other/a/n.md", None),
    ])
    def test_the_longest_matching_folder_wins(self, vault: ObsidianVault, path: str, expected: str | None):
        assert vault._infer_type(PurePosixPath(path)) == expected

    def test_unicode_is_compared_normalized(self, vault: ObsidianVault):
        import unicodedata

        decomposed = unicodedata.normalize("NFD", "Ünï/c/n.md")
        assert decomposed != "Ünï/c/n.md" and vault._infer_type(PurePosixPath(decomposed)) == "z"

    def test_the_defaults_describe_the_pkm(self):
        assert DEFAULT_TYPE_BY_FOLDER["30_Resources/Evergreen"] == "evergreen"
        assert ObsidianSettings().type_by_folder == DEFAULT_TYPE_BY_FOLDER

    def test_the_defaults_are_not_shared_between_settings(self):
        first = ObsidianSettings()
        first.type_by_folder["x"] = "y"
        assert "x" not in ObsidianSettings().type_by_folder

    def test_a_longer_default_folder_beats_its_parent(self, tmp_path: Path):
        write(tmp_path, "10_Projects/Learning roadmaps/R.md")
        write(tmp_path, "10_Projects/Work/P.md")
        vault = vault_of(tmp_path)
        assert vault["R"].inferred_type == "roadmap" and vault["P"].inferred_type == "project"

    def test_the_rules_come_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        from episteme.settings import EpistemeSettings

        monkeypatch.setenv("OBSIDIAN__TYPE_BY_FOLDER", '{"Ideas": "idea"}')
        assert EpistemeSettings(_env_file=None).obsidian.type_by_folder == {"Ideas": "idea"}

    @pytest.mark.parametrize("rules", [{"../x": "y"}, {"a/../../x": "y"}, {"": "x"}, {"/": "x"}, {"a": ""}, {"a": "  "}])
    def test_bad_rules_are_refused(self, rules: dict):
        with pytest.raises(ValueError):
            ObsidianSettings(type_by_folder=rules)

    def test_slashes_around_a_folder_do_not_matter(self):
        assert ObsidianSettings(type_by_folder={"/a/b/": "x"}).type_by_folder == {"a/b": "x"}

    def test_an_attached_note_gets_the_rule_of_this_vault(self, tmp_path: Path):
        vault = vault_of(tmp_path, type_by_folder={"Ideas": "idea"})
        loaded = MarkdownNote(name="n", path="Ideas/n.md")  # as the database returns it: it carries no rule
        assert vault.attach(loaded).inferred_type == "idea"


class TestScanningAVault:
    @pytest.fixture
    def vault(self, tmp_path: Path) -> ObsidianVault:
        write(tmp_path, "30_Resources/Evergreen/AI/Written.md", "type: evergreen\ncreated: 2026-11-10")
        write(tmp_path, "30_Resources/Evergreen/AI/Placeholder.md", None, body="")
        write(tmp_path, "30_Resources/Evergreen/Learning/Design.md", "type: pkm_design")
        write(tmp_path, "20_Lanes/Lane without frontmatter.md", None, body="text")
        write(tmp_path, "00_Inbox/Loose.md", "foo: bar")
        write(tmp_path, "99_Templates/Template.md", None, body="")
        return vault_of(tmp_path)

    def test_types(self, vault: ObsidianVault):
        got = {note.name: note.type for note in vault}
        assert got == {
            "Written": "evergreen", "Placeholder": "evergreen",   # an empty file is still an evergreen note
            "Design": "pkm_design",                               # a written type beats the Evergreen folder
            "Lane without frontmatter": "lane", "Loose": None,
            "Template": "template",                               # an empty file in the templates folder, too
        }

    def test_creation_dates(self, vault: ObsidianVault):
        assert {note.name: note.created for note in vault if note.created} == {"Written": date(2026, 11, 10)}


# ------------------------------------------------------------------------------------ what the database stores


@pytest.fixture(params=["duckdb", "mssql"])
def open_database(request: pytest.FixtureRequest, tmp_path: Path):
    """A function that opens the database of the engine under test, empty on its first call, the same one after."""
    opened: list[Database] = []
    if request.param == "duckdb":
        settings = DataSettings(url=str(tmp_path / "t.duckdb"))
    else:
        settings = DataSettings(engine="mssql", url=request.getfixturevalue("mssql_url"), create_database=True)

    def open_it() -> Database:
        opened.append(Database(settings))
        return opened[-1]

    yield open_it
    for database in opened:
        try:
            database.close()
        except Exception:
            pass


def query(database: Database, sql: str) -> list[dict]:
    """Run SQL written for DuckDB; on SQL Server the tables and views are in the schema of Episteme, so name it."""
    schema = getattr(database.engine, "schema", None)
    if schema:
        sql = re.sub(r"\bFROM (\w+)", rf"FROM [{schema}].\1", sql)
    return database.engine.fetch_all(sql)


@pytest.fixture
def pkm(tmp_path: Path) -> Path:
    root = tmp_path / "pkm"
    write(root, "30_Resources/Evergreen/AI/Written.md", "type: evergreen\ncreated: 2026-11-10\ndomain: ai")
    write(root, "30_Resources/Evergreen/AI/Placeholder.md", None, body="")
    write(root, "30_Resources/Evergreen/Learning/Design.md", "type: pkm_design\ncreated: 2026-08-01")
    write(root, "20_Lanes/Plain lane.md", None, body="no frontmatter")
    write(root, "10_Projects/Episteme.md", "type: project\nstatus: active\ncreated: soon")
    write(root, "00_Inbox/Loose.md", "foo: bar")
    write(root, "99_Templates/v1.0_evergreen_template.md", "type: evergreen\ntemplate_version: 1.0\ncreated: \"{{date}}\"\ndomain: \"{{domain}}\"")
    return root


class TestStoredInTheDatabase:
    def test_type_and_created_are_columns(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        got = {row["name"]: (row["type"], row["created"]) for row in query(database, "SELECT name, type, created FROM notes")}
        assert got == {
            "Written": ("evergreen", date(2026, 11, 10)), "Placeholder": ("evergreen", None),
            "Design": ("pkm_design", date(2026, 8, 1)), "Plain lane": ("lane", None),
            "Episteme": ("project", None),     # `created: soon` is not a date: NULL, not an error
            "Loose": (None, None),
            "v1.0_evergreen_template": ("template", None),     # it writes `type: evergreen`; it is a template
        }

    def test_the_views_read_the_columns(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        assert {row["name"] for row in query(database, "SELECT name FROM evergreen_notes")} == {"Written", "Placeholder"}  # not the template
        assert [row["name"] for row in query(database, "SELECT name FROM vault_notes WHERE type = 'template'")] == ["v1.0_evergreen_template"]
        assert [row["name"] for row in query(database, "SELECT name FROM evergreen_notes WHERE size = 0")] == ["Placeholder"]
        assert {row["name"] for row in query(database, "SELECT name FROM project_notes")} == {"Episteme"}

    def test_a_note_read_back_has_the_stored_type_without_its_file(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        placeholder = database.get_note("30_Resources/Evergreen/AI/Placeholder.md")
        assert placeholder.absolute_path is None and placeholder.type == "evergreen" and placeholder.created is None
        assert database.get_note("30_Resources/Evergreen/AI/Written.md").created == date(2026, 11, 10)

    def test_a_note_read_back_equals_the_one_of_the_vault(self, open_database, pkm: Path):
        database = open_database()
        vault = vault_of(pkm)
        database.sync_notes(vault)
        assert all(vault.attach(note) in vault for note in database.iter_notes())

    def test_a_second_sync_changes_nothing(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        again = database.sync_notes(vault_of(pkm))
        assert (again.added, again.updated, again.removed, again.unchanged) == (0, 0, 0, 7)

    def test_changing_a_rule_updates_only_the_notes_it_touches(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        again = database.sync_notes(vault_of(pkm, type_by_folder={**DEFAULT_TYPE_BY_FOLDER, "00_Inbox": "capture"}))
        assert (again.updated, again.unchanged) == (1, 6)
        assert query(database, "SELECT type FROM notes WHERE name = 'Loose'") == [{"type": "capture"}]

    def test_a_note_that_starts_writing_its_type_keeps_it(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        write(pkm, "30_Resources/Evergreen/AI/Placeholder.md", "type: learning\ncreated: 2026-11-16", body="Now written")
        database.sync_notes(vault_of(pkm))
        assert query(database, "SELECT type, created FROM notes WHERE name = 'Placeholder'") == [{"type": "learning", "created": date(2026, 11, 16)}]


class TestValidationRules:
    """The placeholders have a type now, but they are not notes that forgot to write their properties."""

    def test_an_empty_placeholder_is_not_missing_its_created_date(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        issues = {(row["name"], row["rule"]) for row in query(database, "SELECT * FROM property_issues")}
        assert ("Placeholder", "missing_created") not in issues

    def test_a_typed_folder_note_with_text_but_no_created_is_reported(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        issues = {(row["name"], row["rule"]) for row in query(database, "SELECT * FROM property_issues")}
        assert ("Plain lane", "missing_created") in issues

    def test_a_template_is_a_registered_type_and_is_not_judged_as_an_evergreen_note(self, open_database, pkm: Path):
        """Its `{{date}}` and `{{domain}}` are placeholders, not a malformed date or domain of an evergreen note."""
        database = open_database()
        database.sync_notes(vault_of(pkm))
        issues = {(row["name"], row["rule"]) for row in query(database, "SELECT * FROM property_issues")}
        assert not {rule for name, rule in issues if name == "v1.0_evergreen_template"} & {"unknown_type", "invalid_domain_format"}

    def test_the_placeholders_are_not_listed_as_never_processed(self, open_database, pkm: Path):
        database = open_database()
        database.sync_notes(vault_of(pkm))
        states = {row["name"]: row["anki_state"] for row in query(database, "SELECT name, anki_state FROM evergreen_anki_state")}
        assert states == {"Written": "never"}


# ------------------------------------------------------------------------------------ upgrading a database


class OldDuckDB(DuckDBEngine):
    """DuckDB with the schema as it was before this change: without the last migration."""

    sql_dir_override: Path

    @property
    def sql_dir(self):
        return self.sql_dir_override


class TestUpgradingADatabaseWithNotes:
    def test_duckdb(self, tmp_path: Path, pkm: Path):
        real = Path(str(DuckDBEngine(":memory:").sql_dir))
        old = tmp_path / "old_sql"
        shutil.copytree(real, old)
        for later in ("V10__notes_type_and_created.sql", "V11__create_events.sql", "V12__create_event_views.sql", "V13__create_health_views.sql", "V14__create_rule_detectors.sql", "V15__create_action_views.sql", "V16__health_scope.sql", "V17__chat_source_kind.sql", "V18__empty_list_is_no_value.sql"):
            (old / "migrations" / later).unlink()
        engine = OldDuckDB(str(tmp_path / "old.duckdb"))
        engine.sql_dir_override = old
        Migrations(engine, QueryStore(engine.sql_dir / "queries")).migrate()
        engine.execute("INSERT INTO notes (path, name, category_directory, created_at, updated_at, frontmatter) VALUES "
                       "('a.md', 'a', '00_Inbox', now(), now(), '{\"type\": \"project\", \"created\": \"2026-11-10\"}'), "
                       "('b.md', 'b', '00_Inbox', now(), now(), '{\"type\": [\"x\"], \"created\": \"soon\"}'), "
                       "('c.md', 'c', '00_Inbox', now(), now(), '{}')")
        engine.close()

        with Database(DataSettings(url=str(tmp_path / "old.duckdb"))) as database:
            assert database.schema_version == LATEST_SCHEMA
            got = {row["path"]: (row["type"], row["created"]) for row in query(database, "SELECT path, type, created FROM notes")}
            assert got == {"a.md": ("project", date(2026, 11, 10)), "b.md": (None, None), "c.md": (None, None)}
            assert "idx_notes_type" in database.indexes()
            assert [row["name"] for row in query(database, "SELECT name FROM project_notes")] == ["a"]

    def test_sql_server(self, tmp_path: Path, mssql_url: str):
        pytest.importorskip("mssql_python")
        from episteme.database._mssql import MSSQLEngine
        from episteme.settings import DataSettings as Settings

        settings = Settings(engine="mssql", url=mssql_url, create_database=True)
        old = tmp_path / "old_sql"
        shutil.copytree(Path(str(MSSQLEngine.__new__(MSSQLEngine).sql_dir)), old)  # sql_dir needs no connection
        for later in ("V10__notes_type_and_created.sql", "V11__create_events.sql", "V12__create_event_views.sql", "V13__create_health_views.sql", "V14__create_rule_detectors.sql", "V15__create_action_views.sql", "V16__health_scope.sql", "V17__chat_source_kind.sql", "V18__empty_list_is_no_value.sql"):
            (old / "migrations" / later).unlink()

        class OldEngine(MSSQLEngine):
            @property
            def sql_dir(self):
                return old

        engine = OldEngine.from_settings(settings)
        Migrations(engine, QueryStore(engine.sql_dir / "queries", engine.render)).migrate()
        engine.execute_script(
            "INSERT INTO [episteme].notes (path, name, category_directory, created_at, updated_at, frontmatter) VALUES "
            "(N'a.md', N'a', N'00_Inbox', SYSUTCDATETIME(), SYSUTCDATETIME(), N'{\"type\": \"project\", \"created\": \"2026-11-10\"}'), "
            "(N'b.md', N'b', N'00_Inbox', SYSUTCDATETIME(), SYSUTCDATETIME(), N'{\"type\": [\"x\"], \"created\": \"soon\"}'), "
            "(N'c.md', N'c', N'00_Inbox', SYSUTCDATETIME(), SYSUTCDATETIME(), N'{}')"
        )
        engine.close()

        with Database(settings) as database:
            assert database.schema_version == LATEST_SCHEMA
            got = {row["path"]: (row["type"], row["created"]) for row in query(database, "SELECT path, type, created FROM [episteme].notes")}
            assert got == {"a.md": ("project", date(2026, 11, 10)), "b.md": (None, None), "c.md": (None, None)}
            assert "idx_notes_type" in database.indexes()
            assert [row["name"] for row in query(database, "SELECT name FROM [episteme].project_notes")] == ["a"]
