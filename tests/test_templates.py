"""Which template (and version) a note was created from, filled in from its frontmatter."""

from pathlib import Path

import pytest

from episteme import Episteme
from episteme.cli import DataSettings, EpistemeSettings, ObsidianSettings
from episteme.models.obsidian import CategoryDirectory, MarkdownNote
from episteme.obsidian import ObsidianVault


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_note(tmp_path: Path, frontmatter: str | None, name: str = "n") -> MarkdownNote:
    text = "Body" if frontmatter is None else f"---\n{frontmatter}\n---\nBody"
    return MarkdownNote(name=name, path=f"{name}.md", absolute_path=write(tmp_path / f"{name}.md", text))


class TestParseTemplateVersion:
    @pytest.mark.parametrize(
        ("value", "version"),
        [(1.3, 1.3), (2, 2.0), (0, 0.0), ("1.1", 1.1), ("1", 1.0), (" 2.0 ", 2.0), ("10.25", 10.25)],
    )
    def test_versions(self, value, version: float):
        assert MarkdownNote.parse_template_version(value) == version

    @pytest.mark.parametrize("value", [None, True, False, "", "x", "1.1.0", "v1.0", "1,5", [1.0], {"v": 1}])
    def test_not_versions(self, value):
        assert MarkdownNote.parse_template_version(value) is None


class TestDetectTemplate:
    NAMES = {"evergreen_template", "meeting_input_template"}

    def test_version_and_name(self, tmp_path: Path):
        note = make_note(tmp_path, "type: evergreen\ntemplate_version: 1.3").detect_template(self.NAMES)
        assert (note.template, note.template_version) == ("evergreen_template", 1.3)

    def test_the_version_alone_when_no_template_is_known(self, tmp_path: Path):
        note = make_note(tmp_path, "type: evergreen\ntemplate_version: 1.3").detect_template()
        assert (note.template, note.template_version) == (None, 1.3)

    def test_the_name_needs_an_existing_template(self, tmp_path: Path):
        note = make_note(tmp_path, "type: source\ntemplate_version: 1.1").detect_template(self.NAMES)
        assert (note.template, note.template_version) == (None, 1.1)

    def test_a_note_without_a_version_is_not_from_a_template(self, tmp_path: Path):
        note = make_note(tmp_path, "type: evergreen").detect_template(self.NAMES)
        assert (note.template, note.template_version) == (None, None)

    def test_a_note_without_type_has_only_the_version(self, tmp_path: Path):
        note = make_note(tmp_path, "template_version: 1.0").detect_template(self.NAMES)
        assert (note.template, note.template_version) == (None, 1.0)

    def test_a_note_without_frontmatter(self, tmp_path: Path):
        note = make_note(tmp_path, None).detect_template(self.NAMES)
        assert (note.template, note.template_version) == (None, None)

    @pytest.mark.parametrize("type_value", ["[a, b]", "3", "null"])
    def test_a_type_that_is_not_text(self, tmp_path: Path, type_value: str):
        note = make_note(tmp_path, f"type: {type_value}\ntemplate_version: 1.0").detect_template(self.NAMES)
        assert (note.template, note.template_version) == (None, 1.0)

    def test_a_bare_integer_version(self, tmp_path: Path):
        assert make_note(tmp_path, "type: evergreen\ntemplate_version: 1").detect_template(self.NAMES).template_version == 1.0

    def test_a_template_for_a_type_that_does_not_follow_the_naming(self, tmp_path: Path):
        note = make_note(tmp_path, "type: meeting\ntemplate_version: 1.1")
        assert note.detect_template(self.NAMES).template is None
        assert note.detect_template(self.NAMES, {"meeting": "meeting_input_template"}).template == "meeting_input_template"

    def test_the_mapping_of_a_type_to_a_missing_template_gives_no_name(self, tmp_path: Path):
        note = make_note(tmp_path, "type: meeting\ntemplate_version: 1.1").detect_template(self.NAMES, {"meeting": "nope"})
        assert note.template is None

    def test_it_follows_the_frontmatter_when_called_again(self, tmp_path: Path):
        note = make_note(tmp_path, "type: evergreen\ntemplate_version: 1.3").detect_template(self.NAMES)
        for cached in ("content", "body", "frontmatter"):
            note.__dict__.pop(cached, None)                      # the file is read again
        write(note.absolute_path, "---\ntype: evergreen\n---\nBody")
        assert note.detect_template(self.NAMES).template_version is None and note.template is None

    def test_returns_the_note(self, tmp_path: Path):
        note = make_note(tmp_path, "type: evergreen")
        assert note.detect_template() is note


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    write(root / "99_Templates" / "v1.0_evergreen_template.md", "---\ntype: evergreen\ntemplate_version: 1.0\n---\n")
    write(root / "99_Templates" / "v1.3_evergreen_template.md", "---\ntype: evergreen\ntemplate_version: 1.3\n---\n")
    write(root / "99_Templates" / "v1.1_meeting_input_template.md", "---\ntype: meeting\ntemplate_version: 1.1\n---\n")
    write(root / "30_Resources" / "idea.md", "---\ntype: evergreen\ntemplate_version: 1.0\n---\nIdea")
    write(root / "30_Resources" / "new idea.md", "---\ntype: evergreen\ntemplate_version: 1.3\n---\nIdea")
    write(root / "00_Inbox" / "standup.md", "---\ntype: meeting\ntemplate_version: 1.1\n---\nM")
    write(root / "00_Inbox" / "by hand.md", "---\ntype: evergreen\n---\nNot from a template")
    write(root / "00_Inbox" / "plain.md", "No frontmatter")
    return root


class TestVaultTemplates:
    def test_template_names_drop_the_version_prefix(self, vault_root: Path):
        names = ObsidianVault(ObsidianSettings(vault_root=vault_root)).template_names()
        assert names == {"evergreen_template", "meeting_input_template"}

    def test_a_vault_without_a_templates_folder(self, tmp_path: Path):
        root = tmp_path / "v"
        write(root / "a.md", "A")
        assert ObsidianVault(ObsidianSettings(vault_root=root)).template_names() == set()

    def test_detect_templates(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        assert vault.detect_templates() == 3
        got = {n.name: (n.template, n.template_version) for n in vault}
        assert got["idea"] == ("evergreen_template", 1.0)
        assert got["new idea"] == ("evergreen_template", 1.3)
        assert got["standup"] == (None, 1.1)                       # meeting_input_template is not '<type>_template'
        assert got["by hand"] == (None, None) and got["plain"] == (None, None)

    def test_the_setting_names_the_exceptions(self, vault_root: Path):
        settings = ObsidianSettings(vault_root=vault_root, template_by_type={"meeting": "meeting_input_template"})
        vault = ObsidianVault(settings)
        vault.detect_templates()
        assert vault["standup"].template == "meeting_input_template"

    def test_the_templates_are_not_created_from_a_template(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        vault.detect_templates()
        templates = vault.get_category_dir(CategoryDirectory.TEMPLATES)
        assert all(n.template is None and n.template_version is None for n in templates.iter_notes())

    def test_an_unreadable_note_is_skipped(self, vault_root: Path, caplog):
        (vault_root / "00_Inbox" / "binary.md").write_bytes(b"\xff\xfe\x00")
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        with caplog.at_level("WARNING", logger="episteme"):
            assert vault.detect_templates() == 3
        assert "binary.md" in caplog.text

    def test_mapping_does_not_read_the_notes(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        vault.map()
        assert all(n.template is None and "content" not in n.__dict__ for n in vault)


class TestCreateFromTemplate:
    def test_the_new_note_knows_its_template_and_version(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        note = vault.create_from_template("evergreen_template", "fresh")
        assert (note.template, note.template_version) == ("evergreen_template", 1.3)

    def test_an_older_version(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        note = vault.create_from_template("evergreen_template", "old style", template_version=1.0)
        assert (note.template, note.template_version) == ("evergreen_template", 1.0)

    def test_the_registered_note_is_the_same(self, vault_root: Path):
        vault = ObsidianVault(ObsidianSettings(vault_root=vault_root))
        vault.create_from_template("evergreen_template", "fresh")
        assert vault["fresh"].template == "evergreen_template"


@pytest.fixture
def episteme(vault_root: Path, tmp_path: Path):
    settings = EpistemeSettings(
        obsidian=ObsidianSettings(vault_root=vault_root), data=DataSettings(url=str(tmp_path / "e.duckdb")),
    )
    episteme = Episteme(settings)
    yield episteme
    episteme.data.close()


class TestSync:
    def stored(self, episteme: Episteme) -> dict[str, tuple]:
        rows = episteme.data.engine.fetch_all("SELECT name, template, template_version FROM notes")
        return {r["name"]: (r["template"], r["template_version"]) for r in rows}

    def test_the_database_has_the_template_and_version(self, episteme: Episteme):
        episteme.sync_obsidian()
        stored = self.stored(episteme)
        assert stored["idea"] == ("evergreen_template", 1.0) and stored["new idea"] == ("evergreen_template", 1.3)
        assert stored["standup"] == (None, 1.1) and stored["plain"] == (None, None)
        assert stored["v1.3_evergreen_template"] == (None, None)

    def test_a_note_read_from_the_database_keeps_them(self, episteme: Episteme):
        episteme.sync_obsidian()
        note = episteme.data.get_note("30_Resources/idea.md")
        assert (note.template, note.template_version) == ("evergreen_template", 1.0)

    def test_a_second_sync_changes_nothing(self, episteme: Episteme):
        episteme.sync_obsidian()
        result = episteme.sync_obsidian()
        assert (result.added, result.updated, result.removed) == (0, 0, 0)

    def test_a_changed_version_is_an_update(self, episteme: Episteme, vault_root: Path):
        episteme.sync_obsidian()
        write(vault_root / "30_Resources" / "idea.md", "---\ntype: evergreen\ntemplate_version: 1.3\n---\nIdea")
        assert episteme.sync_obsidian().updated == 1
        assert self.stored(episteme)["idea"] == ("evergreen_template", 1.3)

    def test_the_setting_reaches_the_database(self, vault_root: Path, tmp_path: Path):
        settings = EpistemeSettings(
            obsidian=ObsidianSettings(vault_root=vault_root, template_by_type={"meeting": "meeting_input_template"}),
            data=DataSettings(url=":memory:"),
        )
        episteme = Episteme(settings)
        episteme.sync_obsidian()
        assert self.stored(episteme)["standup"] == ("meeting_input_template", 1.1)
        episteme.data.close()
