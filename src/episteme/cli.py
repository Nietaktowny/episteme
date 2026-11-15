from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, BaseModel

class ObsidianSettings(BaseModel):
    vault_root: Path = Field(".", description="Path to root of Obsidian vault")
    system_dir: Path = Field("_system", description="Path to system directory relative from vault root")
    templates_dir: Path = Field("99_Templates", description="Path to templates directory relative from vault root")
    skip_dirs: list[str] = Field(default_factory=lambda: [".obsidian"], description="List of directories names to skip when mapping the vault")

class EpistemeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_nested_delimiter='__'
    )
    
    obsidian: ObsidianSettings = Field(default_factory=ObsidianSettings, description="Obsidian vault settings")