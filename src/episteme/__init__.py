import os
import logging

from episteme.cli import EpistemeSettings

logger = logging.getLogger("episteme")

class Episteme:
    def __init__(self, settings: EpistemeSettings = None):
        self.settings = settings if settings is not None else EpistemeSettings()
        self._map_vault()
        
    def _map_vault(self):
        tree = {}
        with os.scandir(self.settings.obsidian.vault_root) as entries:
            for entry in entries:
                logger.debug(entry.name)