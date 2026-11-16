import json
import unicodedata
from pathlib import Path, PurePosixPath

import pytest
from pydantic import ValidationError

from episteme.models.obsidian import MarkdownNote, VaultDirectory

NFC = "Zażółć gęślą"
NFD = unicodedata.normalize("NFD", NFC)


class TestPathIsRelativeToTheVault:
    @pytest.mark.parametrize("path", ["a.md", "10_Projects/a.md", "a/b/c/d.md", ".obsidian/x.md", "with space/a b.md"])
    def test_relative_paths_are_accepted(self, path: str):
        assert MarkdownNote(name="a", path=path).path == PurePosixPath(path)

    @pytest.mark.parametrize("path", ["/a.md", "/etc/passwd", "/tmp/vault/a.md"])
    def test_absolute_paths_are_rejected(self, path: str):
        with pytest.raises(ValidationError, match="must be relative to the vault root"):
            MarkdownNote(name="a", path=path)

    @pytest.mark.parametrize("path", ["../a.md", "a/../../b.md", "..", "a/.."])
    def test_paths_leaving_the_vault_are_rejected(self, path: str):
        with pytest.raises(ValidationError, match="must not leave the vault root"):
            MarkdownNote(name="a", path=path)

    def test_the_vault_root_is_a_dot(self):
        assert VaultDirectory(path=".").path == PurePosixPath(".")

    def test_directories_obey_the_same_rules(self):
        with pytest.raises(ValidationError):
            VaultDirectory(path="/abs")
        with pytest.raises(ValidationError):
            VaultDirectory(path="../up")

    @pytest.mark.parametrize("value", ["a/b.md", Path("a/b.md"), PurePosixPath("a/b.md")])
    def test_any_path_flavour_becomes_a_pure_posix_path(self, value):
        path = MarkdownNote(name="b", path=value).path
        assert type(path) is PurePosixPath
        assert str(path) == "a/b.md"

    def test_an_absolute_pathlib_path_is_rejected_too(self, tmp_path: Path):
        with pytest.raises(ValidationError):
            MarkdownNote(name="a", path=tmp_path / "a.md")


class TestUnicodeNormalization:
    def test_path_is_stored_as_nfc(self):
        note = MarkdownNote(name="x", path=f"{NFD}.md")
        assert note.path == PurePosixPath(f"{NFC}.md")
        assert str(note.path) == unicodedata.normalize("NFC", str(note.path))

    def test_name_is_stored_as_nfc(self):
        assert MarkdownNote(name=NFD, path="a.md").name == NFC

    def test_both_spellings_give_equal_notes(self):
        a = MarkdownNote(name=NFC, path=f"{NFC}.md", created_at="2027-01-01", updated_at="2027-01-01")
        b = MarkdownNote(name=NFD, path=f"{NFD}.md", created_at="2027-01-01", updated_at="2027-01-01")
        assert a == b

    def test_directories_are_normalized_too(self):
        assert VaultDirectory(path=f"{NFD}/sub").path == PurePosixPath(f"{NFC}/sub")


class TestAbsolutePath:
    def test_defaults_to_none(self):
        assert MarkdownNote(name="a", path="a.md").absolute_path is None
        assert VaultDirectory(path=".").absolute_path is None

    def test_is_set_by_the_vault_and_may_be_anything_absolute(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.absolute_path == tmp_path / "a.md"

    def test_can_be_attached_later(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md")
        note.absolute_path = tmp_path / "a.md"
        assert note.absolute_path == tmp_path / "a.md"

    def test_is_never_dumped(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert "absolute_path" not in note.model_dump()
        assert "absolute_path" not in json.loads(note.model_dump_json())
        assert str(tmp_path) not in note.model_dump_json()

    def test_is_never_dumped_for_directories_either(self, tmp_path: Path):
        directory = VaultDirectory(path=".", absolute_path=tmp_path)
        assert "absolute_path" not in directory.model_dump()
        assert str(tmp_path) not in directory.model_dump_json()

    def test_is_not_in_the_repr(self, tmp_path: Path):
        assert str(tmp_path) not in repr(MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md"))

    def test_nested_notes_do_not_leak_it_through_their_directory(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        directory = VaultDirectory(path=".", absolute_path=tmp_path, notes=[note])
        assert str(tmp_path) not in json.dumps(directory.model_dump(mode="json"))

    def test_survives_a_copy(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.model_copy().absolute_path == tmp_path / "a.md"


class TestContentNeedsAnAbsolutePath:
    def test_reads_the_file_at_the_absolute_path(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("---\nk: v\n---\nBody", encoding="utf-8")
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "a.md")
        assert note.content.endswith("Body")
        assert note.frontmatter == {"k": "v"}

    def test_the_relative_path_is_not_used_to_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "a.md").write_text("here", encoding="utf-8")
        monkeypatch.chdir(tmp_path)  # a relative read would succeed from here
        with pytest.raises(RuntimeError, match="not attached to a vault"):
            _ = MarkdownNote(name="a", path="a.md").content

    def test_the_error_names_the_note(self):
        with pytest.raises(RuntimeError, match="10_Projects/a.md"):
            _ = MarkdownNote(name="a", path="10_Projects/a.md").content

    def test_frontmatter_also_needs_it_unless_cached(self):
        note = MarkdownNote(name="a", path="a.md")
        with pytest.raises(RuntimeError):
            _ = note.frontmatter
        note.__dict__["frontmatter"] = {"k": "v"}
        assert note.frontmatter == {"k": "v"}

    def test_a_missing_file_still_raises_file_not_found(self, tmp_path: Path):
        note = MarkdownNote(name="a", path="a.md", absolute_path=tmp_path / "gone.md")
        with pytest.raises(FileNotFoundError):
            _ = note.content


class TestVaultDirectoryLookup:
    @pytest.fixture
    def tree(self) -> VaultDirectory:
        deep = VaultDirectory(path="a/b")
        return VaultDirectory(path=".", subdirectories=[VaultDirectory(path="a", subdirectories=[deep]), VaultDirectory(path=NFC)])

    @pytest.mark.parametrize("path", [".", "a", "a/b", NFC, PurePosixPath("a/b"), Path("a/b")])
    def test_finds_directories_by_relative_path(self, tree: VaultDirectory, path):
        assert tree.get_dir(path) is not None

    def test_returns_the_very_directory(self, tree: VaultDirectory):
        assert tree.get_dir("a/b") is tree.subdirectories[0].subdirectories[0]

    @pytest.mark.parametrize("path", ["nope", "a/c", "b", "a/b/c"])
    def test_unknown_directories(self, tree: VaultDirectory, path: str):
        assert tree.get_dir(path) is None

    def test_spelling_of_unicode_names_does_not_matter(self, tree: VaultDirectory):
        assert tree.get_dir(NFD) is tree.get_dir(NFC)

    def test_an_absolute_path_never_matches(self, tree: VaultDirectory):
        assert tree.get_dir("/a") is None

    def test_searching_from_a_subdirectory(self, tree: VaultDirectory):
        assert tree.subdirectories[0].get_dir("a/b") is not None
        assert tree.subdirectories[0].get_dir("a") is tree.subdirectories[0]


class TestRichLabelOfDirectory:
    def test_shows_the_absolute_path_when_attached(self, render, tmp_path: Path):
        assert str(tmp_path) in render(VaultDirectory(path=".", absolute_path=tmp_path), width=200)

    def test_falls_back_to_the_relative_path(self, render):
        text = render(VaultDirectory(path="10_Projects"))
        assert "10_Projects" in text


class TestSplitFrontmatter:
    @pytest.mark.parametrize(
        ("content", "frontmatter", "body"),
        [
            ("---\ntitle: A\n---\nBody", "title: A", "Body"),
            ("---\ntitle: A\n---\n\nTwo\nlines\n", "title: A", "\nTwo\nlines\n"),
            ("---\n---\nBody", "", "Body"),
            ("---\ntitle: A\n---", "title: A", ""),
            ("No frontmatter", "", "No frontmatter"),
            ("", "", ""),
            ("---\ntitle: A\nnever closed", "", "---\ntitle: A\nnever closed"),
            ("text\n---\ntitle: A\n---\nBody", "", "text\n---\ntitle: A\n---\nBody"),
        ],
        ids=["normal", "blank line after", "empty frontmatter", "no body", "none", "empty file", "unclosed", "not at the start"],
    )
    def test_split(self, content: str, frontmatter: str, body: str):
        assert MarkdownNote.split_frontmatter(content) == (frontmatter, body)

    def test_is_what_extract_yaml_frontmatter_parses(self):
        note = MarkdownNote(name="a", path="a.md")
        content = "---\nk: v\n---\nBody"
        assert note.extract_yaml_frontmatter(content) == note.parse_yaml(MarkdownNote.split_frontmatter(content)[0]) == {"k": "v"}


class TestBodyAndHash:
    def note(self, tmp_path: Path, text: str, name: str = "a") -> MarkdownNote:
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
        return MarkdownNote(name=name, path=f"{name}.md", absolute_path=tmp_path / f"{name}.md")

    def test_body_is_the_text_after_the_frontmatter(self, tmp_path: Path):
        assert self.note(tmp_path, "---\nk: v\n---\nHello").body == "Hello"

    def test_a_note_without_frontmatter_is_all_body(self, tmp_path: Path):
        assert self.note(tmp_path, "just text").body == "just text"

    def test_the_hash_is_sha256_hex_of_the_body(self, tmp_path: Path):
        import hashlib

        assert self.note(tmp_path, "---\nk: v\n---\nHello").body_hash == hashlib.sha256(b"Hello").hexdigest()

    def test_changing_only_the_frontmatter_keeps_the_hash(self, tmp_path: Path):
        one = self.note(tmp_path, "---\ntags: [a]\n---\nSame text", "one").body_hash
        two = self.note(tmp_path, "---\ntags: [b, c]\nstatus: done\n---\nSame text", "two").body_hash
        assert one == two

    def test_changing_the_text_changes_the_hash(self, tmp_path: Path):
        one = self.note(tmp_path, "---\nk: v\n---\nHello", "one").body_hash
        two = self.note(tmp_path, "---\nk: v\n---\nHello!", "two").body_hash
        assert one != two

    def test_a_single_character_changes_the_hash_even_when_the_size_stays_the_same(self, tmp_path: Path):
        one = self.note(tmp_path, "Hello world", "one")
        two = self.note(tmp_path, "Hello wurld", "two")
        assert len(one.body) == len(two.body) and one.body_hash != two.body_hash

    def test_adding_frontmatter_to_a_note_keeps_the_hash(self, tmp_path: Path):
        plain = self.note(tmp_path, "Body text", "plain").body_hash
        with_frontmatter = self.note(tmp_path, "---\nk: v\n---\nBody text", "with").body_hash
        assert plain == with_frontmatter

    def test_an_empty_note_has_a_hash_of_the_empty_body(self, tmp_path: Path):
        import hashlib

        assert self.note(tmp_path, "").body_hash == hashlib.sha256(b"").hexdigest()

    def test_unicode(self, tmp_path: Path):
        assert len(self.note(tmp_path, "Zażółć gęślą jaźń 🚀").body_hash) == 64

    def test_both_are_computed_lazily_from_the_file(self, tmp_path: Path):
        note = self.note(tmp_path, "Hello")
        assert "body" not in note.__dict__ and "body_hash" not in note.__dict__
        _ = note.body_hash
        assert "body" in note.__dict__ and "body_hash" in note.__dict__

    def test_the_hash_needs_the_file_unless_it_is_known(self):
        with pytest.raises(RuntimeError, match="not attached"):
            _ = MarkdownNote(name="a", path="a.md").body_hash
        note = MarkdownNote(name="a", path="a.md")
        note.__dict__["body_hash"] = "abc"
        assert note.body_hash == "abc"

    def test_the_hash_is_not_a_field_and_is_not_dumped(self, tmp_path: Path):
        note = self.note(tmp_path, "Hello")
        _ = note.body_hash
        assert "body_hash" not in note.model_dump() and "body" not in note.model_dump()
