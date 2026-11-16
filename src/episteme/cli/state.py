"""What every command shares: the settings it was started with, and the Episteme built from them."""

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import typer

from episteme import Episteme
from episteme.settings import EpistemeSettings


@dataclass
class CliState:
    """Global options of one run, and the lazily created :class:`~episteme.Episteme`.

    Commands never build :class:`~episteme.Episteme` themselves: they ask for ``state.episteme``. That keeps
    the global options (``--env-file``, ``--vault``, ``--db``) in force everywhere, and means a command
    that fails before it needs the database (a typo in an argument) never opens it.

    Attributes:
        env_file: ``.env`` file to read instead of the one in the working directory.
        vault: Overrides ``OBSIDIAN__VAULT_ROOT``.
        db: Overrides ``DATA__URL``.
        json: Print data as JSON instead of tables, for scripts.
        debug: Show the traceback of a failure.
    """

    env_file: Path | None = None
    vault: Path | None = None
    db: str | None = None
    json: bool = False
    debug: bool = False
    _closers: list = field(default_factory=list, repr=False)

    @cached_property
    def settings(self) -> EpistemeSettings:
        """The settings: the file and environment, then the command-line overrides on top."""
        settings = EpistemeSettings(_env_file=self.env_file) if self.env_file else EpistemeSettings()
        if self.vault is not None:
            settings.obsidian.vault_root = self.vault
        if self.db is not None:
            settings.data.url = self.db
        return settings

    @cached_property
    def episteme(self) -> Episteme:
        """Episteme built from :attr:`settings`; its database is closed when the command ends."""
        episteme = Episteme(self.settings)
        self._closers.append(episteme.data.close)
        return episteme

    def close(self) -> None:
        """Release what the command opened."""
        for closer in reversed(self._closers):
            closer()
        self._closers.clear()


def state_of(ctx: typer.Context) -> CliState:
    """The :class:`CliState` of this run, from any command or sub-command."""
    return ctx.find_root().obj
