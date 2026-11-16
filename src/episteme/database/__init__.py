"""Database layer of Episteme: an index of the vault, on a pluggable engine.

* :class:`Database` - the database-neutral API used by the rest of Episteme.
* :mod:`episteme.database.engine` - the contract an engine implements, and
  :func:`~episteme.database.engine.create_engine`, which picks the engine from
  ``DataSettings.engine``.
* :mod:`episteme.database._duckdb` - the DuckDB engine (the only one so far).
* :mod:`episteme.database.migrations` - versioned schema migrations.
* :mod:`episteme.database.queries` - the store of named SQL queries.

All SQL lives in ``sql/<engine>/`` as ``.sql`` files: ``queries/`` for the
statements :class:`Database` runs and ``migrations/`` for the schema, in the
Flyway naming convention (``V1__create_notes.sql``). To support another
database, add an engine class, register it in ``engine.py`` and add its
``sql/<engine>/`` folder; ``Database`` itself does not change.
"""

from episteme.database.database import Database
from episteme.database.engine import DatabaseEngine, create_engine
from episteme.database.migrations import MigrationError, Migrations
from episteme.database.queries import QueryStore

__all__ = ["Database", "DatabaseEngine", "MigrationError", "Migrations", "QueryStore", "create_engine"]
