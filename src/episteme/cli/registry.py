"""How a system adds itself to the command line.

An :class:`Extension` is everything one system contributes: a group of commands, and health checks
for ``episteme doctor``. The built-in systems are listed in :data:`BUILTIN`; other packages can add
theirs without touching Episteme, by declaring an entry point::

    [project.entry-points."episteme.cli"]
    notion = "episteme_notion.cli:extension"

where ``extension`` is an :class:`Extension` (or a function returning one).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points

import typer
from pydantic import BaseModel

from episteme import Episteme

logger = logging.getLogger("episteme")

ENTRY_POINT_GROUP = "episteme.cli"


class CheckResult(BaseModel):
    """Outcome of one health check.

    Attributes:
        name: What was checked (``obsidian: vault``).
        ok: Whether it is fine. A check that does not apply (Anki not configured) is ok, and says so in ``detail``.
        detail: The finding: a path, a version, or what is wrong.
    """

    name: str
    ok: bool
    detail: str = ""


Check = Callable[[Episteme], list[CheckResult]]


@dataclass(frozen=True)
class Extension:
    """One system's part of the command line.

    Attributes:
        name: The word the group is invoked with (``episteme <name> ...``).
        app: The Typer app holding its commands. A group with a callback that has
            ``invoke_without_command=True`` makes ``episteme <name>`` itself a command.
        help: One line for ``episteme --help``.
        checks: Health checks run by ``episteme doctor``; each returns one or more results.
        order: Position in ``--help`` (lower first); ties are by name.
    """

    name: str
    app: typer.Typer
    help: str
    checks: tuple[Check, ...] = field(default=())
    order: int = 100


# Module of each built-in extension; each exposes ``extension``. Adding a system is one line here.
BUILTIN = (
    "episteme.cli.commands.sync",
    "episteme.cli.commands.status",
    "episteme.cli.commands.obsidian",
    "episteme.cli.commands.tasks",
    "episteme.cli.commands.anki",
    "episteme.cli.commands.db",
    "episteme.cli.commands.snapshot",
    "episteme.cli.commands.schedule",
    "episteme.cli.commands.config",
    "episteme.cli.commands.doctor",
    "episteme.cli.commands.tui",
)


def discover() -> list[Extension]:
    """Built-in extensions plus the ones installed packages declare, sorted for ``--help``.

    A plug-in that fails to load is logged and skipped: it must not break the commands that work.
    """
    found: list[Extension] = [import_module(module).extension for module in BUILTIN]
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            loaded = entry.load()
            found.append(loaded() if callable(loaded) and not isinstance(loaded, Extension) else loaded)
        except Exception:
            logger.exception("Could not load the command-line extension '%s'", entry.name)
    return sorted(found, key=lambda extension: (extension.order, extension.name))
