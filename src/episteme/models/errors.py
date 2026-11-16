"""Pydantic model describing an error, independent of the system that raised it."""

import traceback
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class ErrorInfo(BaseModel):
    """A captured error: what it was and where it came from.

    Used wherever an operation reports a failure as data instead of raising,
    for example a failed system inside a sync result.

    Attributes:
        name: Short name of the error: the name of the exception class.
        message: The exception's message.
        traceback: The formatted traceback, ready to print or log.
        occurred_at: When the error was captured.

    Examples:
        >>> try:
        ...     raise FileNotFoundError("a.md is gone")
        ... except Exception as error:
        ...     info = ErrorInfo.from_exception(error)
        >>> info.name, info.message
        ('FileNotFoundError', 'a.md is gone')
        >>> info.traceback.splitlines()[-1]
        'FileNotFoundError: a.md is gone'
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Short name of the error: the exception class name")
    message: str = Field(..., description="The exception's message")
    traceback: str = Field(..., repr=False, description="The formatted traceback")
    occurred_at: datetime = Field(default_factory=datetime.now, description="When the error was captured")

    @classmethod
    def from_exception(cls, error: BaseException) -> "ErrorInfo":
        """Capture an exception, including its traceback and chained causes.

        Args:
            error: The exception, usually from an ``except`` block.
        """
        return cls(
            name=type(error).__name__,
            message=str(error),
            traceback="".join(traceback.format_exception(error)),
        )

    def __rich__(self) -> Panel:
        """Render the error as a red :class:`rich.panel.Panel`: message, time and traceback.

        Returns:
            Panel titled with the error name. The message is shown as plain
            text, so brackets in it are never read as markup.

        Examples:
            >>> isinstance(ErrorInfo(name="OSError", message="[disk] full", traceback="").__rich__(), Panel)
            True
        """
        parts: list[RenderableType] = [
            Text(self.message or "(no message)"),
            Text(f"{self.occurred_at:%Y-%m-%d %H:%M:%S}", style="dim"),
        ]
        if self.traceback.strip():
            parts.append(Syntax(self.traceback.rstrip(), "pytb", word_wrap=True, background_color="default"))
        return Panel(
            Group(*parts),
            title=f"[bold red]{escape(self.name)}[/]",
            border_style="red",
            expand=False,
        )
