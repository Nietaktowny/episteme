from io import StringIO

import pytest
from rich.console import Console

from mssql_server import mssql_server, mssql_shared_url, mssql_url  # noqa: F401 - fixtures of the mssql engine


@pytest.fixture
def render():
    """Render anything rich can print to plain text, as a console of the given width would."""

    def render(renderable, width: int = 80) -> str:
        console = Console(file=StringIO(), width=width, color_system=None, force_terminal=False)
        console.print(renderable)
        return console.file.getvalue()

    return render
