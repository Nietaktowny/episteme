"""Health: the open alerts of the vault, most severe first, from the ``open_alerts`` view."""

from textual.app import ComposeResult

from episteme import Episteme
from episteme.tui.registry import Screen
from episteme.tui.widgets import Pane, QueryTable, rows_of


class HealthPane(Pane):
    def compose(self) -> ComposeResult:
        yield QueryTable("Open alerts", id="alerts")

    def load(self):
        return [alert.model_dump(include={"severity", "rule_id", "rule_name", "entity_id", "detail", "days_open", "remediation"})
                for alert in self.episteme.data.iter_alerts()]

    def show(self, loaded) -> None:
        self.query_one("#alerts", QueryTable).fill(*rows_of([dict(row) for row in loaded]))


def build(episteme: Episteme) -> HealthPane:
    return HealthPane(episteme, id="health")


screen = Screen(name="health", title="Health", factory=build, order=25)
