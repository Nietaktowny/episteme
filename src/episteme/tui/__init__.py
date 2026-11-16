"""The terminal interface (``episteme tui``), built with Textual: ``pip install episteme[tui]``.

Layout::

    app.py        EpistemeApp: tabs, global keys, sync
    registry.py   Screen: how a system adds a tab; built-ins and the ``episteme.tui`` entry points
    widgets.py    Pane (background loading), QueryTable
    screens/      one module per tab, each exposing ``screen``

Screens read through :class:`~episteme.Episteme` and its database views, like the commands do.
"""

from episteme.tui.app import EpistemeApp
from episteme.tui.registry import Screen

__all__ = ["EpistemeApp", "Screen"]
