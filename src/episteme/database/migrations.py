"""Versioned schema migrations, applied by a small runner.

Migration scripts are ``.sql`` files in ``sql/<engine>/migrations/`` named
``V<version>__<description>.sql`` (the Flyway convention). The runner records
every applied script in the ``schema_history`` table, refuses to run when the
history and the scripts disagree, and applies each pending script in its own
transaction together with its history row.

Not supported (yet): repeatable (``R__``) and undo (``U__``) scripts.
"""

import time

from episteme.database.engine import DatabaseEngine
from episteme.database.queries import QueryStore
from episteme.models.database import AppliedMigration, Migration


class MigrationError(RuntimeError):
    """The migration scripts and the database's schema history cannot be reconciled."""


class Migrations:
    """Applies the migrations of an engine to its database.

    Args:
        engine: Engine of the database to migrate; its ``sql/<name>/migrations/``
            folder holds the scripts.
        queries: Queries of the engine; the runner uses ``history_create``,
            ``history_list`` and ``history_insert``.

    Examples:
        >>> from episteme.cli import DataSettings
        >>> from episteme.database.engine import create_engine
        >>> engine = create_engine(DataSettings(url=":memory:"))
        >>> migrations = Migrations(engine, QueryStore(engine.sql_dir / "queries"))
        >>> pending = [m.version for m in migrations.pending()]
        >>> pending[:2]
        ['1', '2']
        >>> [m.version for m in migrations.migrate()] == pending
        True
        >>> migrations.pending(), migrations.current_version() == pending[-1]
        ([], True)
        >>> engine.close()
    """

    def __init__(self, engine: DatabaseEngine, queries: QueryStore):
        self.engine = engine
        self.queries = queries

    def available(self) -> list[Migration]:
        """The migration scripts found for the engine, ordered by version.

        Raises:
            MigrationError: If a script has an invalid name, two scripts share
                a version, or a repeatable/undo script is found.
        """
        migrations: dict[str, Migration] = {}
        for resource in self.engine.sql_dir.joinpath("migrations").iterdir():
            if not resource.name.endswith(".sql"):
                continue
            if resource.name.startswith(("R__", "U__")):
                raise MigrationError(f"Repeatable and undo migrations are not supported: '{resource.name}'.")
            try:
                migration = Migration.from_script(resource.name, resource.read_text(encoding="utf-8"))
            except ValueError as error:
                raise MigrationError(str(error)) from error
            if migration.version in migrations:
                raise MigrationError(
                    f"Version {migration.version} is used by both '{migrations[migration.version].script}' "
                    f"and '{migration.script}'."
                )
            migrations[migration.version] = migration
        return sorted(migrations.values(), key=lambda migration: migration.version_key)

    def applied(self) -> list[AppliedMigration]:
        """The migrations recorded in the database's schema history, in the order they were applied."""
        self.engine.execute(self.queries["history_create"])
        rows = self.engine.fetch_all(self.queries["history_list"])
        return [AppliedMigration.model_validate(row) for row in rows]

    def current_version(self) -> str | None:
        """Version of the latest applied migration, or ``None`` if none was applied."""
        applied = self.applied()
        if not applied:
            return None
        return max(applied, key=lambda migration: migration.version_key).version

    def pending(self) -> list[Migration]:
        """The migrations that have not been applied yet, in the order they would run.

        Raises:
            MigrationError: If the history and the scripts disagree (see :meth:`validate`).
        """
        applied = self.applied()
        available = self.available()
        self._validate(applied, available)
        applied_versions = {migration.version for migration in applied}
        return [migration for migration in available if migration.version not in applied_versions]

    def validate(self) -> None:
        """Check the schema history against the scripts.

        Raises:
            MigrationError: If an applied migration has no script (the
                database was migrated by a newer Episteme), a script was
                modified after it was applied, or an unapplied script is older
                than the latest applied one.
        """
        self._validate(self.applied(), self.available())

    @staticmethod
    def _validate(applied: list[AppliedMigration], available: list[Migration]) -> None:
        scripts = {migration.version: migration for migration in available}
        for record in applied:
            migration = scripts.get(record.version)
            if migration is None:
                raise MigrationError(
                    f"The database has migration {record.version} ('{record.description}') applied, "
                    "which this version of Episteme does not know: the database is newer."
                )
            if migration.checksum != record.checksum:
                raise MigrationError(
                    f"Migration {record.version} ('{record.script}') was modified after it was applied "
                    "(checksum mismatch)."
                )
        if applied:
            latest = max(record.version_key for record in applied)
            applied_versions = {record.version for record in applied}
            for migration in available:
                if migration.version not in applied_versions and migration.version_key < latest:
                    raise MigrationError(
                        f"Migration {migration.version} ('{migration.script}') is older than the latest "
                        "applied migration but was not applied (out of order)."
                    )

    def migrate(self) -> list[Migration]:
        """Apply all pending migrations, oldest first.

        Each migration runs in its own transaction together with its history
        row, so a failing script leaves the database as it was before that
        script. Scripts applied before the failure stay applied.

        Returns:
            The migrations that were applied (empty if the database was up to date).

        Raises:
            MigrationError: If the history and the scripts disagree.
        """
        pending = self.pending()
        rank = len(self.applied())
        for migration in pending:
            rank += 1
            started = time.perf_counter()
            with self.engine.transaction():
                self.engine.execute_script(migration.sql)
                self.engine.execute(
                    self.queries["history_insert"],
                    {
                        "installed_rank": rank,
                        "version": migration.version,
                        "description": migration.description,
                        "script": migration.script,
                        "checksum": migration.checksum,
                        "execution_time_ms": round((time.perf_counter() - started) * 1000),
                    },
                )
        return pending
