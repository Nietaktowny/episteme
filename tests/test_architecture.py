"""Guards for the dependency direction between Episteme's parts.

    settings             <- used by everyone, imports nothing of Episteme
    models/              <- shared vocabulary, imports no system
    obsidian  -> settings, models.obsidian        (knows nothing about the database)
    anki      -> settings, models.anki            (knows nothing about the database)
    database  -> settings, models                 (knows nothing about Obsidian)
    episteme (package root), cli/, tui/           (the only places that know several systems)

The imports are analysed statically (``ast``), so the rules hold without running
the code. What cannot be seen: dynamic imports such as the engine registry's
``importlib.import_module``, which is intentional, and the implicit import of a
parent package: importing ``episteme.obsidian`` always runs ``episteme/__init__``,
the composition root, first. Only explicit imports count as dependencies.
"""

import ast
from collections import deque
from pathlib import Path

import pytest

import episteme

PACKAGE = "episteme"
SOURCE = Path(episteme.__file__).parent


class ImportGraph:
    """Which modules of a package import which others, found by parsing the source.

    Args:
        source: Folder of the package (the one holding its ``__init__.py``).
        package: Name of the package.
    """

    def __init__(self, source: Path, package: str = PACKAGE):
        self.package = package
        self.files = {self._module_name(path, source, package): path for path in source.rglob("*.py")}
        self.imports: dict[str, set[str]] = {name: self._parse(name, path) for name, path in self.files.items()}

    @staticmethod
    def _module_name(path: Path, source: Path, package: str) -> str:
        parts = [package, *path.relative_to(source).with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _parse(self, module: str, path: Path) -> set[str]:
        """Internal modules imported by ``module``, anywhere in its code (docstrings and strings excluded)."""
        is_package = path.name == "__init__.py"
        found: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve_from(module, is_package, node)
                found.add(base)
                for alias in node.names:  # "from package import submodule"
                    found.add(f"{base}.{alias.name}")
        return {name for name in found if name in self.files and name != module}

    @staticmethod
    def _resolve_from(module: str, is_package: bool, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        parts = module.split(".")
        if not is_package:
            parts = parts[:-1]
        parts = parts[: len(parts) - (node.level - 1)]
        return ".".join([*parts, *([node.module] if node.module else [])])

    def reachable(self, module: str) -> set[str]:
        """Every module ``module`` depends on, directly or through other modules."""
        seen: set[str] = set()
        queue = deque(self.imports[module])
        while queue:
            current = queue.popleft()
            if current not in seen:
                seen.add(current)
                queue.extend(self.imports[current])
        return seen

    def path(self, source: str, target: str) -> list[str]:
        """Shortest chain of imports from ``source`` to ``target`` (empty if there is none)."""
        queue = deque([[source]])
        seen = {source}
        while queue:
            chain = queue.popleft()
            for imported in sorted(self.imports[chain[-1]]):
                if imported == target:
                    return [*chain, imported]
                if imported not in seen:
                    seen.add(imported)
                    queue.append([*chain, imported])
        return []

    def cycles(self) -> list[list[str]]:
        """Import cycles, each as a list of modules (found with Tarjan's algorithm)."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        components: list[list[str]] = []

        def visit(module: str) -> None:
            index[module] = low[module] = len(index)
            stack.append(module)
            on_stack.add(module)
            for imported in self.imports[module]:
                if imported not in index:
                    visit(imported)
                    low[module] = min(low[module], low[imported])
                elif imported in on_stack:
                    low[module] = min(low[module], index[imported])
            if low[module] == index[module]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == module:
                        break
                if len(component) > 1:
                    components.append(sorted(component))

        for module in sorted(self.imports):
            if module not in index:
                visit(module)
        return components

    def violations(self, allowed: dict[str, set[str]]) -> list[str]:
        """Dependencies that the rules do not allow.

        Args:
            allowed: For a module name prefix, the module prefixes it may depend
                on (directly or indirectly). A module follows the rule of its
                longest matching prefix; modules without a rule are unrestricted.
        """
        problems = []
        for module in sorted(self.imports):
            rule = max((prefix for prefix in allowed if self.matches(module, prefix)), key=len, default=None)
            if rule is None:
                continue
            for dependency in sorted(self.reachable(module)):
                if not any(self.matches(dependency, permitted) for permitted in allowed[rule]):
                    chain = " -> ".join(self.path(module, dependency))
                    problems.append(f"{module} must not depend on {dependency} ({chain})")
        return problems

    @staticmethod
    def matches(module: str, prefix: str) -> bool:
        return module == prefix or module.startswith(f"{prefix}.")


# Interfaces on top of Episteme: they compose the systems, so they may import the package root.
COMPOSING = ("episteme.cli", "episteme.tui")


# Each rule lists what a part may depend on. Anything not listed is forbidden.
RULES: dict[str, set[str]] = {
    "episteme.settings": set(),
    "episteme.models": {"episteme.models"},
    "episteme.obsidian": {"episteme.settings", "episteme.models.obsidian", "episteme.obsidian"},
    "episteme.anki": {"episteme.settings", "episteme.models.anki", "episteme.anki"},
    "episteme.database": {"episteme.settings", "episteme.models", "episteme.database"},
}
# "episteme" (the package root), "episteme.__main__" and "episteme.cli" have no rule: they compose the parts.


@pytest.fixture(scope="module")
def graph() -> ImportGraph:
    return ImportGraph(SOURCE)


class TestEpistemeArchitecture:
    def test_the_graph_sees_the_modules(self, graph: ImportGraph):
        assert {"episteme", "episteme.settings", "episteme.obsidian", "episteme.database.database",
                "episteme.models.obsidian", "episteme.models.database"} <= set(graph.files)

    def test_the_graph_sees_real_imports(self, graph: ImportGraph):
        assert "episteme.settings" in graph.imports["episteme.obsidian"]
        assert "episteme.models.obsidian" in graph.imports["episteme.database.database"]

    def test_no_circular_imports(self, graph: ImportGraph):
        assert graph.cycles() == []

    def test_every_part_depends_only_on_what_it_may(self, graph: ImportGraph):
        assert graph.violations(RULES) == []

    def test_obsidian_knows_nothing_about_the_database(self, graph: ImportGraph):
        reachable = graph.reachable("episteme.obsidian")
        assert not {m for m in reachable if m.startswith(("episteme.database", "episteme.models.database"))}

    def test_database_knows_nothing_about_obsidian(self, graph: ImportGraph):
        for module in (m for m in graph.files if m.startswith("episteme.database")):
            assert "episteme.obsidian" not in graph.reachable(module), module

    def test_models_import_no_system(self, graph: ImportGraph):
        for module in (m for m in graph.files if m.startswith("episteme.models")):
            outside = {d for d in graph.reachable(module) if not d.startswith("episteme.models")}
            assert outside == set(), module

    def test_only_the_package_root_knows_both_systems(self, graph: ImportGraph):
        knows_both = {
            module
            for module in graph.files
            if {"episteme.obsidian", "episteme.database"} <= graph.reachable(module)
        }
        composing = {m for m in knows_both if not m.startswith(COMPOSING)}
        assert composing <= {"episteme", "episteme.__main__"}

    def test_no_module_imports_the_package_root(self, graph: ImportGraph):
        importers = {module for module, imported in graph.imports.items() if "episteme" in imported}
        outside_cli = {m for m in importers if not m.startswith(COMPOSING)}
        assert outside_cli <= {"episteme.__main__"}


@pytest.fixture
def package(tmp_path: Path):
    """Build a small throwaway package from ``{"relative/path.py": "source"}``."""

    def build(files: dict[str, str]) -> ImportGraph:
        root = tmp_path / "toy"
        for relative, source in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        return ImportGraph(root, package="toy")

    return build


class TestImportGraph:
    """The guard is only worth something if it really notices violations."""

    def test_finds_direct_imports(self, package):
        graph = package({"__init__.py": "", "a.py": "import toy.b", "b.py": ""})
        assert graph.imports["toy.a"] == {"toy.b"}

    def test_finds_from_imports(self, package):
        graph = package({"__init__.py": "", "a.py": "from toy.b import thing", "b.py": ""})
        assert graph.imports["toy.a"] == {"toy.b"}

    def test_from_package_import_module(self, package):
        graph = package({"__init__.py": "", "a.py": "from toy import b", "b.py": ""})
        assert "toy.b" in graph.imports["toy.a"]

    def test_import_inside_a_function_counts(self, package):
        graph = package({"__init__.py": "", "a.py": "def f():\n    from toy import b\n", "b.py": ""})
        assert "toy.b" in graph.imports["toy.a"]

    def test_relative_imports(self, package):
        graph = package({
            "__init__.py": "", "sub/__init__.py": "from .inner import x", "sub/inner.py": "from ..top import y",
            "top.py": "",
        })
        assert graph.imports["toy.sub"] == {"toy.sub.inner"}
        assert graph.imports["toy.sub.inner"] == {"toy.top"}

    def test_docstrings_and_strings_are_not_imports(self, package):
        source = '"""\n>>> from toy.b import x\n"""\nTEXT = "import toy.b"\n'
        graph = package({"__init__.py": "", "a.py": source, "b.py": ""})
        assert graph.imports["toy.a"] == set()

    def test_external_imports_are_ignored(self, package):
        graph = package({"__init__.py": "", "a.py": "import os\nimport duckdb\nfrom pathlib import Path"})
        assert graph.imports["toy.a"] == set()

    def test_transitive_dependencies_and_their_path(self, package):
        graph = package({"__init__.py": "", "a.py": "import toy.b", "b.py": "import toy.c", "c.py": ""})
        assert graph.reachable("toy.a") == {"toy.b", "toy.c"}
        assert graph.path("toy.a", "toy.c") == ["toy.a", "toy.b", "toy.c"]
        assert graph.path("toy.c", "toy.a") == []

    def test_detects_cycles(self, package):
        graph = package({"__init__.py": "", "a.py": "import toy.b", "b.py": "import toy.c", "c.py": "import toy.a"})
        assert graph.cycles() == [["toy.a", "toy.b", "toy.c"]]

    def test_no_cycles_in_a_tree(self, package):
        graph = package({"__init__.py": "", "a.py": "import toy.b", "b.py": ""})
        assert graph.cycles() == []

    def test_detects_a_forbidden_direct_dependency(self, package):
        graph = package({"__init__.py": "", "one.py": "import toy.two", "two.py": ""})
        assert graph.violations({"toy.one": set()}) == ["toy.one must not depend on toy.two (toy.one -> toy.two)"]

    def test_detects_a_forbidden_transitive_dependency(self, package):
        graph = package({"__init__.py": "", "one.py": "import toy.mid", "mid.py": "import toy.two", "two.py": ""})
        problems = graph.violations({"toy.one": {"toy.mid"}})
        assert problems == ["toy.one must not depend on toy.two (toy.one -> toy.mid -> toy.two)"]

    def test_allowed_dependencies_pass(self, package):
        graph = package({"__init__.py": "", "one.py": "import toy.two", "two.py": ""})
        assert graph.violations({"toy.one": {"toy.two"}}) == []

    def test_rule_of_the_longest_prefix_wins(self, package):
        graph = package({
            "__init__.py": "", "pkg/__init__.py": "", "pkg/loose.py": "import toy.other",
            "pkg/strict.py": "import toy.other", "other.py": "",
        })
        rules = {"toy.pkg": {"toy.other"}, "toy.pkg.strict": set()}
        assert graph.violations(rules) == ["toy.pkg.strict must not depend on toy.other (toy.pkg.strict -> toy.other)"]

    def test_prefix_match_is_by_whole_name(self, package):
        graph = package({"__init__.py": "", "one.py": "import toy.database_extra", "database_extra.py": ""})
        assert graph.violations({"toy.one": {"toy.database"}}) != []

    def test_modules_without_a_rule_are_unrestricted(self, package):
        graph = package({"__init__.py": "import toy.a", "a.py": ""})
        assert graph.violations({"toy.a": set()}) == []
