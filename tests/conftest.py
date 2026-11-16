from io import StringIO

import pytest
from rich.console import Console


@pytest.fixture
def render():
    """Render anything rich can print to plain text, as a console of the given width would."""

    def render(renderable, width: int = 80) -> str:
        console = Console(file=StringIO(), width=width, color_system=None, force_terminal=False)
        console.print(renderable)
        return console.file.getvalue()

    return render
