"""How a system adds a screen to the TUI; the counterpart of :mod:`episteme.cli.registry`.

The built-in screens are listed in :data:`BUILTIN`. Another package adds its own with an entry point::

    [project.entry-points."episteme.tui"]
    notion = "episteme_notion.tui:screen"

where ``screen`` is a :class:`Screen` (or a function returning one).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points

from textual.widget import Widget

from episteme import Episteme

logger = logging.getLogger("episteme")

ENTRY_POINT_GROUP = "episteme.tui"


@dataclass(frozen=True)
class Screen:
    """One tab of the TUI.

    Attributes:
        name: Identifier of the tab; also what the tab is addressed by.
        title: Label shown on the tab.
        factory: Builds the tab's widget from Episteme. The widget is mounted once; make it a
            :class:`~episteme.tui.widgets.Pane` to get loading in the background and refreshing.
        order: Position of the tab (lower first); ties are by name. The tab's key is its position.
    """

    name: str
    title: str
    factory: Callable[[Episteme], Widget]
    order: int = 100


BUILTIN = (
    "episteme.tui.screens.dashboard",
    "episteme.tui.screens.obsidian",
    "episteme.tui.screens.anki",
    "episteme.tui.screens.sql",
)


def discover() -> list[Screen]:
    """Built-in screens plus the ones installed packages declare; one that fails to load is skipped."""
    found: list[Screen] = [import_module(module).screen for module in BUILTIN]
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            loaded = entry.load()
            found.append(loaded() if callable(loaded) and not isinstance(loaded, Screen) else loaded)
        except Exception:
            logger.exception("Could not load the TUI screen '%s'", entry.name)
    return sorted(found, key=lambda screen: (screen.order, screen.name))
