import pytest
from pydantic import ValidationError

from episteme.models.database import AppliedMigration, Migration


class TestMigrationFromScript:
    @pytest.mark.parametrize(
        ("script", "version", "description"),
        [
            ("V1__create_notes.sql", "1", "create notes"),
            ("V2_1__add_tags_table.sql", "2.1", "add tags table"),
            ("V2.1__dotted.sql", "2.1", "dotted"),
            ("V10__big_number.sql", "10", "big number"),
            ("V1_2_3__three_parts.sql", "1.2.3", "three parts"),
            ("V1__keeps__double__underscores.sql", "1", "keeps  double  underscores"),
        ],
    )
    def test_parses_name(self, script: str, version: str, description: str):
        migration = Migration.from_script(script, "SELECT 1")
        assert (migration.version, migration.description, migration.script) == (version, description, script)

    @pytest.mark.parametrize(
        "script",
        ["1__a.sql", "V__a.sql", "Va__x.sql", "V1_a.sql", "V1__.sql", "V1__a.txt", "v1__a.sql", "V1__a.sql.bak", "R__a.sql", ""],
    )
    def test_invalid_names_rejected(self, script: str):
        with pytest.raises(ValueError, match="not a valid migration file name"):
            Migration.from_script(script, "SELECT 1")

    def test_keeps_sql(self):
        assert Migration.from_script("V1__a.sql", "SELECT 1;").sql == "SELECT 1;"

    def test_sql_not_in_repr(self):
        assert "SELECT" not in repr(Migration.from_script("V1__a.sql", "SELECT 1;"))

    def test_is_frozen(self):
        migration = Migration.from_script("V1__a.sql", "x")
        with pytest.raises(ValidationError):
            migration.version = "2"


class TestMigrationChecksum:
    def test_same_sql_same_checksum(self):
        assert Migration.from_script("V1__a.sql", "x").checksum == Migration.from_script("V1__a.sql", "x").checksum

    def test_different_sql_different_checksum(self):
        assert Migration.from_script("V1__a.sql", "x").checksum != Migration.from_script("V1__a.sql", "y").checksum

    def test_line_endings_do_not_matter(self):
        unix = Migration.from_script("V1__a.sql", "a\nb\n")
        windows = Migration.from_script("V1__a.sql", "a\r\nb\r\n")
        assert unix.checksum == windows.checksum
        assert windows.sql == "a\nb\n"

    def test_file_name_does_not_matter(self):
        assert Migration.from_script("V1__a.sql", "x").checksum == Migration.from_script("V2__b.sql", "x").checksum

    def test_is_sha256_hex(self):
        checksum = Migration.from_script("V1__a.sql", "x").checksum
        assert len(checksum) == 64
        int(checksum, 16)


class TestVersionOrdering:
    def test_version_key(self):
        assert Migration.from_script("V2_10__a.sql", "x").version_key == (2, 10)

    def test_numeric_not_lexical_order(self):
        scripts = ["V10__a.sql", "V2_10__a.sql", "V2_9__a.sql", "V1__a.sql", "V2__a.sql"]
        migrations = sorted((Migration.from_script(s, "x") for s in scripts), key=lambda m: m.version_key)
        assert [m.version for m in migrations] == ["1", "2", "2.9", "2.10", "10"]

    @pytest.mark.parametrize("version", ["", "a", "1.", ".1", "1..2", "1.a", "-1", "1 2"])
    def test_invalid_version_rejected(self, version: str):
        with pytest.raises(ValidationError):
            Migration(version=version, description="d", script="s", checksum="c", sql="x")


class TestAppliedMigration:
    VALUES = dict(
        installed_rank=1, version="1", description="d", script="V1__d.sql", checksum="c",
        installed_on="2027-01-02T03:04:05", execution_time_ms=7,
    )

    def test_validates_a_history_row(self):
        migration = AppliedMigration.model_validate(self.VALUES)
        assert migration.installed_on.year == 2027
        assert migration.version_key == (1,)

    @pytest.mark.parametrize("field", ["installed_rank", "execution_time_ms"])
    def test_rejects_invalid_numbers(self, field: str):
        values = {**self.VALUES, field: -1 if field == "execution_time_ms" else 0}
        with pytest.raises(ValidationError):
            AppliedMigration.model_validate(values)

    def test_rejects_invalid_version(self):
        with pytest.raises(ValidationError):
            AppliedMigration.model_validate({**self.VALUES, "version": "x"})

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            AppliedMigration.model_validate(self.VALUES).version = "2"


class TestRich:
    def test_migration_is_a_panel_naming_version_and_description(self, render):
        from rich.panel import Panel

        migration = Migration.from_script("V2__add_tags.sql", "CREATE TABLE tags (n TEXT);\nSELECT 1;")
        assert isinstance(migration.__rich__(), Panel)
        text = render(migration)
        assert "V2" in text and "add tags" in text
        assert "V2__add_tags.sql" in text
        assert migration.checksum[:12] in text

    def test_migration_shows_the_line_count_but_not_the_sql(self, render):
        text = render(Migration.from_script("V1__a.sql", "CREATE TABLE secret_table (n TEXT);\nSELECT 1;"))
        assert "secret_table" not in text
        assert "2" in text

    def test_migration_markup_is_not_interpreted(self, render):
        text = render(Migration.from_script("V1__[bold]odd[/bold].sql", "x"))
        assert "[bold]odd[/bold]" in text

    def test_applied_migration(self, render):
        from rich.panel import Panel

        applied = AppliedMigration(
            installed_rank=2, version="2", description="add tags", script="V2__add_tags.sql",
            checksum="ab" * 32, installed_on="2027-01-02T03:04:05", execution_time_ms=12,
        )
        assert isinstance(applied.__rich__(), Panel)
        text = render(applied)
        for expected in ("V2", "add tags", "V2__add_tags.sql", "2027-01-02 03:04:05", "12 ms", "ababababab", "rank"):
            assert expected in text
