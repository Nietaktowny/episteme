"""Store of named SQL queries, kept as ``.sql`` files."""

from importlib.resources.abc import Traversable


class QueryStore:
    """Loads named SQL queries from the ``.sql`` files of a folder.

    Each file holds one query and is addressed by its file name without the
    extension. Queries are read once and cached.

    Parameters in the queries use the engine-neutral ``$name`` style; engines
    translate it when their driver needs another style.

    Args:
        location: Folder with the ``.sql`` files: a path, or a resource folder
            of a package (see :func:`importlib.resources.files`).

    Examples:
        >>> from importlib.resources import files
        >>> store = QueryStore(files("episteme.database") / "sql" / "duckdb" / "queries")
        >>> "notes_count" in store
        True
        >>> store["notes_count"]
        'SELECT count(*) AS count FROM notes'
        >>> "no_such_query" in store
        False
    """

    def __init__(self, location: Traversable):
        self.location = location
        self._cache: dict[str, str] = {}

    def __getitem__(self, name: str) -> str:
        """Get the SQL text of a query.

        Args:
            name: File name of the query without the ``.sql`` extension.

        Returns:
            The SQL text, stripped of surrounding whitespace.

        Raises:
            KeyError: If there is no such query file.
        """
        if name not in self._cache:
            resource = self.location.joinpath(f"{name}.sql")
            if not resource.is_file():
                raise KeyError(f"No query named '{name}' in '{self.location}'.")
            self._cache[name] = resource.read_text(encoding="utf-8").strip()
        return self._cache[name]

    def __contains__(self, name: object) -> bool:
        """Whether a query with this name exists."""
        if not isinstance(name, str):
            return False
        try:
            self[name]
        except KeyError:
            return False
        return True

    def names(self) -> list[str]:
        """Names of all available queries, sorted."""
        return sorted(
            resource.name.removesuffix(".sql")
            for resource in self.location.iterdir()
            if resource.name.endswith(".sql")
        )
