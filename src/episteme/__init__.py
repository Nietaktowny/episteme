"""Episteme: a toolkit that brings your knowledge systems together.

The :class:`Episteme` class is the entry point. It groups the supported
systems, each living in its own module:

* Obsidian - :class:`episteme.obsidian.ObsidianVault`

Data models are defined in :mod:`episteme.models` and configuration in
:mod:`episteme.cli`.
"""

from episteme.cli import EpistemeSettings
from episteme.obsidian import ObsidianVault

__all__ = ["Episteme", "EpistemeSettings", "ObsidianVault"]

class Episteme:
    """Entry point grouping all knowledge systems.

    Attributes:
        settings: Settings shared by all systems.
        obsidian: Mapped Obsidian vault.

    Examples:
        >>> import tempfile
        >>> from episteme.cli import ObsidianSettings
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root=tmp))
        ...     episteme = Episteme(settings)
        ...     episteme.settings is settings
        ...     isinstance(episteme.obsidian, ObsidianVault)
        ...     episteme.obsidian.statistics.total_notes
        True
        True
        0
    """

    def __init__(self, settings: EpistemeSettings = None):
        """Initialize all systems.

        Args:
            settings: Settings to use. When ``None``, default
                :class:`EpistemeSettings` (environment / ``.env``) are used.
        """
        self.settings: EpistemeSettings = settings if settings is not None else EpistemeSettings()
        self.obsidian: ObsidianVault = ObsidianVault(self.settings)
