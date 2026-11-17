"""Health of the vault: the open alerts, and what a health check found."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class Alert(BaseModel):
    """An open violation of a health rule, in the active vault: a row of the ``open_alerts`` view.

    Attributes:
        violation_id: Id of the ``health.violation_opened`` event that began it.
        rule_id: The rule, e.g. ``R9``.
        rule_name: The rule's short name, e.g. ``unresolved_lane``.
        severity: ``info``, ``warn`` or ``error``.
        severity_rank: 1, 2 or 3.
        entity_id: What it is about (a note path).
        note_name: The note's name, if it is still in the database.
        note_type: The note's type when the violation was found.
        detail: What exactly is wrong (a property, a value); empty when the rule says it all.
        opened_at: When the violation was first seen (UTC).
        days_open: How many days it has been open.
        reopen_count: How many times it was fixed and came back before this episode.
        is_baseline: It was found by the first check of the database, so it may be older than ``opened_at``.
        description: What the rule checks.
        remediation: What to do about it.
    """

    model_config = ConfigDict(frozen=True)

    violation_id: str
    rule_id: str
    rule_name: str
    severity: str
    severity_rank: int
    entity_id: str
    note_name: str | None = None
    note_type: str | None = None
    detail: str = ""
    opened_at: datetime
    days_open: int
    reopen_count: int = 0
    is_baseline: bool = False
    description: str
    remediation: str


class HealthResult(BaseModel):
    """What a health check did.

    Attributes:
        run_id: Groups the events the check recorded.
        checked_at: When it ran (UTC).
        findings: Violations found right now, archive included.
        opened: Violations that were not open before.
        resolved: Violations that are no longer found.
        escalated: Violations raised one severity level because they have been open long enough (rule E1).
        open_total: Violations open after the check, archive included.
        alerts: Open violations in the active vault, by severity.
        delivered: Alerts sent to sinks after the check.
        delivery_failed: Alerts that could not be sent (they are tried again at the next check).
        baseline: It was the first check of this database: everything found counts as new, and is marked as baseline.
        duration_seconds: How long it took.

    Examples:
        >>> result = HealthResult(run_id="r", checked_at=datetime(2026, 1, 1), findings=3, opened=1, resolved=2, open_total=3,
        ...                       alerts={"error": 1, "warn": 2})
        >>> result.alert_count
        3
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    checked_at: datetime
    findings: int = 0
    opened: int = 0
    resolved: int = 0
    escalated: int = 0
    open_total: int = 0
    alerts: dict[str, int] = Field(default_factory=dict)
    delivered: int = 0
    delivery_failed: int = 0
    baseline: bool = False
    duration_seconds: float = 0.0

    @property
    def alert_count(self) -> int:
        """Open violations in the active vault."""
        return sum(self.alerts.values())

    def __rich__(self) -> Panel:
        """A panel with what the check found and what changed."""
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column()
        for label, value in (("found", self.findings), ("opened", self.opened), ("resolved", self.resolved), ("escalated", self.escalated),
                             ("open alerts", ", ".join(f"{n} {s}" for s, n in self.alerts.items()) or "none"),
                             *((("sent", f"{self.delivered} ({self.delivery_failed} failed)"),) if self.delivered or self.delivery_failed else ())):
            grid.add_row(label, f"[bold]{value}[/]")
        parts: list[RenderableType] = [grid]
        if self.baseline:
            parts.append(Text("First check of this database: every violation is new, and marked as baseline.", style="dim"))
        return Panel(Group(*parts), title="Health check", expand=False)
