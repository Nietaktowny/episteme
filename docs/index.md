# Episteme
Episteme is supposed to be my data collection and management system for my PKM and all of it's parts like Anki or Obsidian. 


```python
from episteme import Episteme
from episteme.cli import EpistemeSettings, ObsidianSettings

settings = EpistemeSettings(obsidian=ObsidianSettings(vault_root="~/vault"))
episteme = Episteme(settings)
print(episteme.obsidian)
```

See the [API reference](reference/episteme.md) for details.
