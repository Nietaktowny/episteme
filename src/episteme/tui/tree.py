"""The vault as a folder tree."""

from dataclasses import dataclass, field

from rich.text import Text
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

# Expanding every folder is only sensible for a short list; a long one is opened by the user.
EXPAND_ALL_UP_TO = 300


@dataclass
class Folder:
    """A folder of the vault: its sub-folders, the notes directly in it, and how many notes are below it."""

    name: str
    folders: dict[str, "Folder"] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class Note:
    """A note in the tree: its path relative to the vault."""

    path: str


def build_folders(paths: list[str]) -> Folder:
    """The folder structure of vault-relative note paths.

    Examples:
        >>> root = build_folders(["a.md", "x/b.md", "x/y/c.md"])
        >>> root.count, sorted(root.folders), root.notes, root.folders["x"].count
        (3, ['x'], ['a.md'], 2)
    """
    root = Folder("")
    for path in paths:
        parts = path.split("/")
        folder = root
        folder.count += 1
        for part in parts[:-1]:
            folder = folder.folders.setdefault(part, Folder(part))
            folder.count += 1
        folder.notes.append(path)
    return root


class NoteTree(Tree):
    """Explorer of the vault: folders first, then notes, filled in as folders are opened.

    A vault can hold hundreds of thousands of notes, so a folder's children are created when it is
    expanded, not when the tree is loaded.
    """

    def __init__(self, **kwargs):
        super().__init__("vault", **kwargs)
        self.show_root = True

    def set_paths(self, paths: list[str], expand_all: bool | None = None) -> None:
        """Show these notes. With ``expand_all`` (default: when there are few) every folder is open."""
        self.clear()
        root = build_folders(paths)
        self.root.set_label(Text.assemble(("vault ", "bold"), (f"({root.count})", "dim")))
        self.root.data = root
        self._populate(self.root)
        self.root.expand()
        if expand_all if expand_all is not None else len(paths) <= EXPAND_ALL_UP_TO:
            self._expand_below(self.root)

    def _populate(self, node: TreeNode) -> None:
        folder = node.data
        if not isinstance(folder, Folder) or node.children:
            return
        for name in sorted(folder.folders, key=str.lower):
            child = folder.folders[name]
            node.add(Text.assemble((name, "bold"), (f"  {child.count}", "dim")), data=child)
        for path in sorted(folder.notes, key=str.lower):
            node.add_leaf(path.rsplit("/", 1)[-1].removesuffix(".md"), data=Note(path))

    def _expand_below(self, node: TreeNode) -> None:
        for child in node.children:
            if isinstance(child.data, Folder):
                self._populate(child)
                child.expand()
                self._expand_below(child)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        self._populate(event.node)

    def select_path(self, path: str) -> bool:
        """Open the folders down to a note and put the cursor on it. Returns whether it was found."""
        node = self.root
        parts = path.split("/")
        for part in parts[:-1]:
            self._populate(node)
            node = next((child for child in node.children if isinstance(child.data, Folder) and child.data.name == part), None)
            if node is None:
                return False
            node.expand()
        self._populate(node)
        leaf = next((child for child in node.children if isinstance(child.data, Note) and child.data.path == path), None)
        if leaf is None:
            return False
        self.move_cursor(leaf)
        return True
