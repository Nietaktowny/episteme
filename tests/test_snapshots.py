"""Snapshots: the state of the vault kept in a Parquet file, to compare against later."""

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from episteme import Episteme
from episteme.settings import DataSettings, EpistemeSettings, ObsidianSettings
from episteme.database import Database
from episteme.models.database import Snapshot
from episteme.obsidian import ObsidianVault

COLUMNS = ["path", "name", "in_archive", "is_empty", "size", "type", "created", "status", "body_hash", "frontmatter"]


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "laptop_one" / "Drive" / "vault"
    root.mkdir(parents=True)
    write(root, "10_Projects/Episteme.md", 'type: project\nstatus: active\ncreated: 2026-10-01\nlane: "[[L]]"'.join(["---\n", "\n---\nProject body"]))
    write(root, "30_Resources/Evergreen/AI/Attention.md", "---\ntype: evergreen\ncreated: 2026-11-10\ndomain: ai\nankified_date: null\n---\nHello world")
    write(root, "30_Resources/Evergreen/AI/Zażółć.md", "---\ntype: evergreen\ncreated: 2026-11-11\n---\nZ")
    write(root, "30_Resources/Evergreen/Code/TODO.md", "")
    write(root, "00_Inbox/capture.md", "no frontmatter at all")
    return root


@pytest.fixture
def vault(vault_root: Path) -> ObsidianVault:
    return ObsidianVault(ObsidianSettings(vault_root=vault_root))


@pytest.fixture
def database(vault: ObsidianVault):
    with Database(DataSettings(url=":memory:")) as database:
        database.sync_notes(vault)
        yield database


def read(database: Database, file: Path) -> list[dict]:
    return database.engine.fetch_all(f"SELECT * FROM read_parquet('{file}')")


class TestDatabaseSnapshot:
    def test_writes_a_parquet_file(self, database: Database, tmp_path: Path):
        snapshot = database.snapshot(tmp_path / "notes.parquet")
        data = (tmp_path / "notes.parquet").read_bytes()
        assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"
        assert snapshot.path == tmp_path / "notes.parquet"

    def test_describes_what_it_wrote(self, database: Database, tmp_path: Path):
        before = datetime.now()
        snapshot = database.snapshot(tmp_path / "notes.parquet")
        assert isinstance(snapshot, Snapshot)
        assert snapshot.notes == database.count_notes() == 5
        assert snapshot.size_bytes == (tmp_path / "notes.parquet").stat().st_size > 0
        assert before <= snapshot.taken_at <= datetime.now()

    def test_has_exactly_these_columns_in_this_order(self, database: Database, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        described = database.engine.fetch_all(f"DESCRIBE SELECT * FROM read_parquet('{tmp_path / 'notes.parquet'}')")
        assert [row["column_name"] for row in described] == COLUMNS

    def test_one_row_per_note_ordered_by_path(self, database: Database, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        paths = [row["path"] for row in read(database, tmp_path / "notes.parquet")]
        assert paths == sorted(paths) and len(paths) == 5

    def test_the_values_are_the_decoded_ones(self, database: Database, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        rows = {row["path"]: row for row in read(database, tmp_path / "notes.parquet")}
        row = rows["30_Resources/Evergreen/AI/Attention.md"]
        assert (row["name"], row["type"], row["created"], row["status"]) == ("Attention", "evergreen", date(2026, 11, 10), None)
        assert (row["in_archive"], row["is_empty"]) == (False, False)
        assert row["size"] == (database.get_note(row["path"]).size)
        assert json.loads(row["frontmatter"]) == {"type": "evergreen", "created": "2026-11-10", "domain": "ai", "ankified_date": None}

    def test_the_body_hash_is_the_notes_hash(self, database: Database, vault: ObsidianVault, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        rows = {row["path"]: row["body_hash"] for row in read(database, tmp_path / "notes.parquet")}
        assert rows["30_Resources/Evergreen/AI/Attention.md"] == vault["Attention"].body_hash

    def test_placeholders_and_untyped_notes_are_included(self, database: Database, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        rows = {row["name"]: row for row in read(database, tmp_path / "notes.parquet")}
        assert rows["TODO"]["is_empty"] is True and rows["TODO"]["type"] == "evergreen"  # the folder says what an empty note is
        assert rows["capture"]["type"] is None and json.loads(rows["capture"]["frontmatter"]) == {}

    def test_unicode_names_are_kept(self, database: Database, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        assert "30_Resources/Evergreen/AI/Zażółć.md" in {row["path"] for row in read(database, tmp_path / "notes.parquet")}

    def test_nothing_in_it_depends_on_the_machine(self, database: Database, vault_root: Path, tmp_path: Path):
        database.snapshot(tmp_path / "notes.parquet")
        rows = read(database, tmp_path / "notes.parquet")
        text = repr(rows)
        assert str(vault_root) not in text and "laptop_one" not in text and str(tmp_path) not in text
        assert all(not row["path"].startswith("/") for row in rows)
        assert not {"modified_at", "created_at", "updated_at", "absolute_path"} & set(rows[0])

    def test_the_same_vault_somewhere_else_gives_the_same_snapshot(self, vault: ObsidianVault, vault_root: Path, database: Database, tmp_path: Path):
        copy = tmp_path / "laptop_two" / "somewhere" / "vault"
        shutil.copytree(vault_root, copy)
        database.snapshot(tmp_path / "one.parquet")
        with Database(DataSettings(url=":memory:")) as other:
            other.sync_notes(ObsidianVault(ObsidianSettings(vault_root=copy)))
            other.snapshot(tmp_path / "two.parquet")
        assert read(database, tmp_path / "one.parquet") == read(database, tmp_path / "two.parquet")

    def test_missing_folders_are_created(self, database: Database, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c" / "notes.parquet"
        database.snapshot(target)
        assert target.is_file()

    def test_an_empty_database_gives_a_valid_empty_snapshot(self, tmp_path: Path):
        with Database(DataSettings(url=":memory:")) as empty:
            snapshot = empty.snapshot(tmp_path / "empty.parquet")
            assert snapshot.notes == 0
            assert read(empty, tmp_path / "empty.parquet") == []
            described = empty.engine.fetch_all(f"DESCRIBE SELECT * FROM read_parquet('{tmp_path / 'empty.parquet'}')")
            assert [r["column_name"] for r in described] == COLUMNS

    def test_it_holds_the_database_not_the_vault(self, database: Database, vault_root: Path, tmp_path: Path):
        write(vault_root, "00_Inbox/new.md", "created after the last sync")
        database.snapshot(tmp_path / "notes.parquet")
        assert "new" not in {row["name"] for row in read(database, tmp_path / "notes.parquet")}

    def test_accepts_a_string_path(self, database: Database, tmp_path: Path):
        assert database.snapshot(str(tmp_path / "notes.parquet")).path == tmp_path / "notes.parquet"


class TestSnapshotsAreKept:
    def test_an_existing_snapshot_is_not_replaced(self, database: Database, tmp_path: Path):
        target = tmp_path / "notes.parquet"
        database.snapshot(target)
        before = target.read_bytes()
        with pytest.raises(FileExistsError, match="already exists"):
            database.snapshot(target)
        assert target.read_bytes() == before

    def test_it_can_be_replaced_on_request(self, database: Database, tmp_path: Path):
        target = tmp_path / "notes.parquet"
        database.snapshot(target)
        database.delete_note("00_Inbox/capture.md")
        assert database.snapshot(target, overwrite=True).notes == 4
        assert len(read(database, target)) == 4

    def test_no_partial_file_is_left_after_success(self, database: Database, tmp_path: Path):
        out = tmp_path / "out"
        database.snapshot(out / "notes.parquet")
        assert [p.name for p in out.iterdir()] == ["notes.parquet"]

    def test_a_failed_export_leaves_nothing_behind(self, database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        def broken(sql, path, params=None):
            Path(path).write_bytes(b"half a file")
            raise RuntimeError("disk full")

        monkeypatch.setattr(database.engine, "export_parquet", broken)
        out = tmp_path / "out"
        with pytest.raises(RuntimeError, match="disk full"):
            database.snapshot(out / "notes.parquet")
        assert list(out.iterdir()) == []   # neither the snapshot nor the half-written file

    def test_a_failed_overwrite_keeps_the_old_snapshot(self, database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "out" / "notes.parquet"
        database.snapshot(target)
        before = target.read_bytes()
        monkeypatch.setattr(database.engine, "export_parquet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            database.snapshot(target, overwrite=True)
        assert target.read_bytes() == before
        assert [p.name for p in target.parent.iterdir()] == ["notes.parquet"]


class TestComparingWithASnapshot:
    """The reason snapshots exist: what changed since the last review?"""

    @pytest.fixture
    def changed(self, vault: ObsidianVault, vault_root: Path, database: Database, tmp_path: Path) -> Database:
        database.snapshot(tmp_path / "previous.parquet")
        (vault_root / "30_Resources/Evergreen/AI/Attention.md").write_text("---\ntype: evergreen\ncreated: 2026-11-10\ndomain: ai\nankified_date: null\n---\nHello wurld")  # same size
        (vault_root / "30_Resources/Evergreen/AI/Zażółć.md").write_text("---\ntype: evergreen\ncreated: 2026-11-11\nstatus: done\n---\nZ")  # only properties
        (vault_root / "10_Projects/Episteme.md").rename(vault_root / "Episteme moved.md")
        write(vault_root, "30_Resources/Evergreen/Code/New TODO.md", "")
        (vault_root / "00_Inbox/capture.md").unlink()
        vault.map()
        database.sync_notes(vault)
        database.engine.execute(f"CREATE VIEW previous AS SELECT * FROM read_parquet('{tmp_path / 'previous.parquet'}')")
        return database

    def names(self, database: Database, sql: str) -> list[str]:
        return [row["name"] for row in database.engine.fetch_all(sql)]

    def test_new_notes(self, changed: Database):
        assert self.names(changed, "SELECT n.name FROM vault_notes n ANTI JOIN previous USING (path) ORDER BY 1") == ["Episteme moved", "New TODO"]

    def test_notes_that_are_gone(self, changed: Database):
        assert self.names(changed, "SELECT p.name FROM previous p ANTI JOIN vault_notes USING (path) ORDER BY 1") == ["Episteme", "capture"]

    def test_new_empty_notes(self, changed: Database):
        assert self.names(changed, "SELECT n.name FROM vault_notes n ANTI JOIN previous USING (path) WHERE n.is_empty") == ["New TODO"]

    def test_a_text_edit_is_found_even_though_the_size_did_not_change(self, changed: Database):
        edited = changed.engine.fetch_all("SELECT n.name, n.size = p.size AS same_size FROM vault_notes n JOIN previous p USING (path) WHERE n.body_hash <> p.body_hash")
        assert edited == [{"name": "Attention", "same_size": True}]

    def test_a_change_of_properties_is_not_an_edit_of_the_text(self, changed: Database):
        assert "Zażółć" not in self.names(changed, "SELECT n.name FROM vault_notes n JOIN previous p USING (path) WHERE n.body_hash <> p.body_hash")
        assert self.names(changed, "SELECT n.name FROM vault_notes n JOIN previous p USING (path) WHERE n.frontmatter::VARCHAR <> p.frontmatter ORDER BY 1") == ["Zażółć"]

    def test_a_move_or_rename_is_recognised_by_an_unchanged_body_type_and_created(self, changed: Database):
        """The note was renamed and moved; its text, type and creation date are what identify it."""
        moved = changed.engine.fetch_all("""
            SELECT p.path AS before, n.path AS after
            FROM (SELECT * FROM previous ANTI JOIN vault_notes USING (path)) p
            JOIN (SELECT * FROM vault_notes ANTI JOIN previous USING (path)) n
              ON p.body_hash = n.body_hash AND p.type IS NOT DISTINCT FROM n.type AND p.created IS NOT DISTINCT FROM n.created
            ORDER BY 1""")
        assert moved == [{"before": "10_Projects/Episteme.md", "after": "Episteme moved.md"}]

    def test_empty_notes_cannot_be_matched_that_way(self, vault: ObsidianVault, vault_root: Path, database: Database, tmp_path: Path):
        """Every empty note has the same hash, so deleting one placeholder and adding another looks like a move."""
        database.snapshot(tmp_path / "previous.parquet")
        (vault_root / "30_Resources/Evergreen/Code/TODO.md").unlink()
        write(vault_root, "30_Resources/Evergreen/Code/Something else.md", "")
        vault.map()
        database.sync_notes(vault)
        database.engine.execute(f"CREATE VIEW previous AS SELECT * FROM read_parquet('{tmp_path / 'previous.parquet'}')")
        looks_moved = database.engine.fetch_all("""
            SELECT p.name AS before, n.name AS after
            FROM (SELECT * FROM previous ANTI JOIN vault_notes USING (path)) p
            JOIN (SELECT * FROM vault_notes ANTI JOIN previous USING (path)) n ON p.body_hash = n.body_hash AND p.is_empty AND n.is_empty""")
        assert looks_moved == [{"before": "TODO", "after": "Something else"}]

    def test_stock_numbers_against_the_previous_state(self, changed: Database):
        row = changed.engine.fetch_one("SELECT (SELECT count(*) FROM previous WHERE is_empty) AS before, (SELECT count(*) FROM vault_notes WHERE is_empty) AS now")
        assert row == {"before": 1, "now": 2}

    def test_every_snapshot_in_a_folder_reads_as_one_table(self, changed: Database, tmp_path: Path):
        changed.snapshot(tmp_path / "later.parquet")
        rows = changed.engine.fetch_all(
            f"SELECT regexp_extract(filename, '([a-z]+)\\.parquet', 1) AS snapshot, count(*) AS notes, "
            f"count(*) FILTER (is_empty) AS empty FROM read_parquet('{tmp_path}/*.parquet', filename = true) GROUP BY 1 ORDER BY 1")
        assert rows == [{"snapshot": "later", "notes": 5, "empty": 2}, {"snapshot": "previous", "notes": 5, "empty": 1}]


class TestSnapshotModel:
    VALUES = dict(path=Path("a.parquet"), notes=3, size_bytes=1024, taken_at=datetime(2026, 11, 20, 8, 30))

    def test_fields(self):
        snapshot = Snapshot(**self.VALUES)
        assert (snapshot.notes, snapshot.size_bytes) == (3, 1024)

    @pytest.mark.parametrize("field", ["notes", "size_bytes"])
    def test_negative_numbers_rejected(self, field: str):
        with pytest.raises(ValidationError):
            Snapshot(**{**self.VALUES, field: -1})

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            Snapshot(**self.VALUES).notes = 9

    def test_renders(self, render):
        text = render(Snapshot(**self.VALUES))
        assert "Snapshot" in text and "a.parquet" in text and "1.0 KiB" in text and "2026-11-20 08:30:00" in text and "3" in text


def episteme_for(vault_root: Path, **obsidian) -> Episteme:
    return Episteme(EpistemeSettings(
        obsidian=ObsidianSettings(vault_root=vault_root, **obsidian),
        data=DataSettings(url=":memory:"),
    ))


class TestEpistemeSnapshot:
    def test_writes_into_the_vault_under_the_snapshots_folder(self, vault_root: Path):
        snapshot = episteme_for(vault_root).snapshot("2026-11-20")
        assert snapshot.path == vault_root / "_system" / "episteme" / "snapshots" / "notes_2026-11-20.parquet"
        assert snapshot.path.is_file()

    def test_the_label_defaults_to_todays_date(self, vault_root: Path):
        snapshot = episteme_for(vault_root).snapshot()
        assert snapshot.path.name == f"notes_{date.today().isoformat()}.parquet"

    def test_it_syncs_first_so_it_shows_the_vault_as_it_is_now(self, vault_root: Path):
        episteme = episteme_for(vault_root)
        write(vault_root, "00_Inbox/just added.md", "x")
        snapshot = episteme.snapshot("now")
        assert snapshot.notes == 6
        assert "just added" in {row["name"] for row in read(episteme.data, snapshot.path)}

    def test_a_second_snapshot_with_the_same_label_is_refused(self, vault_root: Path):
        episteme = episteme_for(vault_root)
        episteme.snapshot("review")
        with pytest.raises(FileExistsError):
            episteme.snapshot("review")

    def test_overwrite_replaces_it(self, vault_root: Path):
        episteme = episteme_for(vault_root)
        episteme.snapshot("review")
        write(vault_root, "00_Inbox/more.md", "x")
        assert episteme.snapshot("review", overwrite=True).notes == 6

    def test_different_labels_give_different_files(self, vault_root: Path):
        episteme = episteme_for(vault_root)
        episteme.snapshot("one"), episteme.snapshot("two")
        assert sorted(p.name for p in (vault_root / "_system" / "episteme" / "snapshots").iterdir()) == ["notes_one.parquet", "notes_two.parquet"]

    @pytest.mark.parametrize("label", ["", "../escape", "a/b", ".hidden", "with space", "semi;colon", "tab\t", "new\nline", "Zażółć"])
    def test_invalid_labels_are_refused_before_anything_happens(self, vault_root: Path, label: str):
        episteme = episteme_for(vault_root)
        with pytest.raises(ValueError, match="Invalid snapshot label"):
            episteme.snapshot(label)
        assert not (vault_root / "_system").exists() and not episteme.obsidian.is_mapped

    @pytest.mark.parametrize("label", ["2026-11-20", "review_01", "v1.2", "A", "week-47"])
    def test_valid_labels(self, vault_root: Path, label: str):
        assert episteme_for(vault_root).snapshot(label).path.name == f"notes_{label}.parquet"

    def test_the_folder_can_be_changed(self, vault_root: Path):
        snapshot = episteme_for(vault_root, snapshots_dir=Path("Reviews/data")).snapshot("x")
        assert snapshot.path == vault_root / "Reviews" / "data" / "notes_x.parquet"

    def test_the_snapshot_is_not_a_note(self, vault_root: Path):
        episteme = episteme_for(vault_root)
        before = len(episteme.obsidian)
        episteme.snapshot("x")
        episteme.obsidian.map()
        assert len(episteme.obsidian) == before

    def test_a_vault_that_does_not_exist_creates_nothing(self, tmp_path: Path):
        episteme = episteme_for(tmp_path / "missing")
        with pytest.raises(FileNotFoundError):
            episteme.snapshot("x")
        assert not (tmp_path / "missing").exists()

    def test_the_snapshot_is_the_same_on_another_machine(self, vault_root: Path, tmp_path: Path):
        copy = tmp_path / "other" / "vault"
        shutil.copytree(vault_root, copy)
        one, two = episteme_for(vault_root), episteme_for(copy)
        first, second = one.snapshot("x"), two.snapshot("x")
        assert read(one.data, first.path) == read(two.data, second.path)


class TestSnapshotsDirSetting:
    def test_default(self):
        assert ObsidianSettings().snapshots_dir == Path("_system/episteme/snapshots")

    @pytest.mark.parametrize("folder", ["/abs/folder", "..", "../outside", "a/../../b"])
    def test_a_folder_outside_the_vault_is_rejected(self, folder: str):
        with pytest.raises(ValidationError, match="inside the vault"):
            ObsidianSettings(snapshots_dir=folder)

    def test_from_the_environment(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OBSIDIAN__SNAPSHOTS_DIR", "Elsewhere/snaps")
        assert EpistemeSettings().obsidian.snapshots_dir == Path("Elsewhere/snaps")


class TestMigrationFiveOnAnExistingDatabase:
    """A database created before body_hash existed keeps its notes and gets the new column."""

    def test_upgrade(self, tmp_path: Path):
        from episteme.database._duckdb import DuckDBEngine
        from episteme.database.migrations import Migrations
        from episteme.database.queries import QueryStore

        real = Path(str(DuckDBEngine(":memory:").sql_dir))
        old_sql = tmp_path / "sql"
        shutil.copytree(real, old_sql)
        for later in ("V5__add_body_hash.sql", "V6__create_tasks.sql", "V7__create_task_views.sql", "V8__create_anki.sql", "V9__create_anki_views.sql", "V10__notes_type_and_created.sql", "V11__create_events.sql", "V12__create_event_views.sql", "V13__create_health_views.sql", "V14__create_rule_detectors.sql", "V15__create_action_views.sql", "V16__health_scope.sql", "V17__chat_source_kind.sql", "V18__empty_list_is_no_value.sql"):
            (old_sql / "migrations" / later).unlink()                          # the schema before body_hash

        class OldEngine(DuckDBEngine):
            @property
            def sql_dir(self):
                return old_sql

        url = tmp_path / "old.duckdb"
        engine = OldEngine(str(url))
        Migrations(engine, QueryStore(engine.sql_dir / "queries")).migrate()
        engine.execute("INSERT INTO notes (path,name,category_directory,created_at,updated_at,frontmatter) VALUES ('a.md','a','00_Inbox',now(),now(),'{\"type\":\"evergreen\"}')")
        assert "body_hash" not in {r["column_name"] for r in engine.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_name = 'notes'")}
        engine.close()

        with Database(DataSettings(url=str(url))) as database:
            assert [m.version for m in database.migrations.applied()][4] == "5"
            assert database.engine.fetch_all("SELECT path, body_hash FROM notes") == [{"path": "a.md", "body_hash": None}]
            assert "body_hash" in {r["column_name"] for r in database.engine.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_name = 'vault_notes'")}
            assert database.engine.fetch_all("SELECT name FROM evergreen_notes") == [{"name": "a"}]


class TestTypedViewsKeepUpWithVaultNotes:
    """DuckDB expands `*` when a view is created, so a view can silently miss a later column."""

    TYPED = ["evergreen_notes", "evergreen_placeholders", "project_notes", "source_notes", "daily_notes", "weekly_reviews"]

    @pytest.mark.parametrize("view", TYPED)
    def test_carries_every_column_of_vault_notes(self, view: str):
        with Database(DataSettings(url=":memory:")) as database:
            def columns(name):
                return {r["column_name"] for r in database.engine.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_name = $n", {"n": name})}
            assert columns("vault_notes") <= columns(view)
