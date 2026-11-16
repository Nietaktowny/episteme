# Episteme
Episteme is supposed to be my data collection and management system for my PKM and all of it's parts like Anki or Obsidian. 


```python
from episteme import Episteme
from episteme.settings import EpistemeSettings, ObsidianSettings

settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root="~/vault"))
episteme = Episteme(settings)
print(episteme.obsidian)
```

Anki statistics (never the content of the cards) can be synced into the same database: see [Anki](anki.md).

See the [API reference](reference/episteme.md) for details.
