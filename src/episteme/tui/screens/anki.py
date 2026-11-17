"""Anki: today's work and the decks, from the database's Anki views."""

from textual.app import ComposeResult

from episteme import Episteme
from episteme.tui.registry import Screen
from episteme.tui.widgets import Pane, QueryTable, rows_of


class AnkiPane(Pane):
    def compose(self) -> ComposeResult:
        yield QueryTable("Today", id="today")
        yield QueryTable("Decks", id="decks")

    def load(self):
        data = self.episteme.data
        return data.anki_today(), data.anki_decks()

    def show(self, loaded) -> None:
        today, decks = loaded
        self.query_one("#today", QueryTable).fill(*rows_of([dict(row) for row in today]))
        self.query_one("#decks", QueryTable).fill(*rows_of([dict(row) for row in decks]))


def build(episteme: Episteme) -> AnkiPane:
    return AnkiPane(episteme, id="anki")


screen = Screen(name="anki", title="Anki", factory=build, order=30)
