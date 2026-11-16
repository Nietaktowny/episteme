"""``episteme anki``: the Anki collection, read-only."""

from typing import Annotated

import typer

from episteme import Episteme
from episteme.anki import AnkiCollectionNotFoundError, AnkiContentDisabledError
from episteme.cli.output import emit, errors, fail, table
from episteme.cli.registry import CheckResult, Extension
from episteme.cli.state import state_of

app = typer.Typer(help="Inspect the Anki collection (read-only).", no_args_is_help=True)


@app.command()
def info(ctx: typer.Context) -> None:
    """Collection-level facts: schema, scheduler, FSRS, rollover hour."""
    state = state_of(ctx)
    try:
        with state.episteme.anki.snapshot() as snapshot:
            data = snapshot.info()
    except AnkiCollectionNotFoundError as error:
        raise fail(str(error)) from None
    emit(state, data, table("Anki collection", ["Property", "Value"], list(data.model_dump().items())))


@app.command()
def decks(ctx: typer.Context) -> None:
    """The deck tree."""
    state = state_of(ctx)
    try:
        with state.episteme.anki.snapshot() as snapshot:
            found = snapshot.decks()
    except AnkiCollectionNotFoundError as error:
        raise fail(str(error)) from None
    emit(state, found, table("Decks", ["Id", "Name", "Filtered"], [[deck.deck_id, deck.name, deck.is_filtered] for deck in found]))


@app.command()
def content(ctx: typer.Context, note_ids: Annotated[list[int], typer.Argument(help="Anki note ids.")]) -> None:
    """The text of notes (needs ANKI__ALLOW_CONTENT=true; never stored)."""
    state = state_of(ctx)
    try:
        found = list(state.episteme.anki.note_contents(note_ids))
    except AnkiContentDisabledError:
        raise fail("Reading note content is off. Set ANKI__ALLOW_CONTENT=true to allow it.") from None
    except AnkiCollectionNotFoundError as error:
        raise fail(str(error)) from None
    rows = [[item.note_id, item.note_type_name, " | ".join(item.fields.values())] for item in found]
    emit(state, found, table("Notes", ["Id", "Type", "Fields"], rows))


_STATES = ("new", "learning", "review", "relearning")


@app.command()
def cards(
    ctx: typer.Context,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Cards whose note has this tag (or a tag below it: 'lang' matches 'lang::pl'). Repeat to require several.")] = None,
    deck: Annotated[str | None, typer.Option("--deck", "-d", help="Cards of this deck and its sub-decks.")] = None,
    card_state: Annotated[str | None, typer.Option("--state", "-s", help="new, learning, review or relearning.")] = None,
    due: Annotated[bool, typer.Option("--due", help="Only cards due now.")] = False,
    leeches: Annotated[bool, typer.Option("--leeches", help="Only leeches.")] = False,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Only cards whose note text contains this (reads the collection: needs ANKI__ALLOW_CONTENT).")] = None,
    where: Annotated[str | None, typer.Option("--where", help="Extra SQL condition on the view anki_card_details, e.g. \"lapses > 3\".")] = None,
    show_content: Annotated[bool, typer.Option("--content", help="Show the text of the notes (needs ANKI__ALLOW_CONTENT).")] = False,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Cards to show (0 for all).")] = 50,
) -> None:
    """Find cards by tag, deck, state or text. Scheduling comes from the database (as of the last Anki sync)."""
    state = state_of(ctx)
    episteme = state.episteme
    conditions, params = [], {}
    for index, name in enumerate(tag or []):
        conditions.append(f"len(list_filter(tags, lambda t: lower(t) = lower($tag{index}) OR starts_with(lower(t), lower($tag{index}) || '::'))) > 0")
        params[f"tag{index}"] = name
    if deck:
        conditions.append("(deck_name = $deck OR starts_with(deck_name, $deck || '::'))")
        params["deck"] = deck
    if card_state:
        if card_state not in _STATES:
            raise fail(f"'{card_state}' is not a state; use {', '.join(_STATES)}.")
        conditions.append("state = $state")
        params["state"] = card_state
    if due:
        conditions.append("is_due")
    if leeches:
        conditions.append("is_leech")
    if where:
        if ";" in where:
            raise fail("--where takes one condition, without ';'.")
        conditions.append(f"({where})")
    sql = "SELECT card_id, note_id, deck_name, state, due_date, interval_days, lapses, tags FROM anki_card_details"
    sql += (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY due_date NULLS LAST, card_id"
    needs_text = bool(query) or show_content
    if not needs_text and limit:
        sql += f" LIMIT {int(limit)}"
    try:
        rows = [dict(row) for row in episteme.data.engine.fetch_all(sql, params)]
    except Exception as error:
        raise fail(f"{type(error).__name__}: {error}") from None
    texts: dict[int, str] = {}
    if needs_text:
        try:
            for content in episteme.anki.note_contents({row["note_id"] for row in rows}):
                texts[content.note_id] = " | ".join(content.fields.values())
        except AnkiContentDisabledError:
            raise fail("Reading note content is off. Set ANKI__ALLOW_CONTENT=true to allow it.") from None
        except AnkiCollectionNotFoundError as error:
            raise fail(str(error)) from None
        if query:
            rows = [row for row in rows if query.lower() in texts.get(row["note_id"], "").lower()]
        if limit:
            rows = rows[:limit]
        if show_content:
            for row in rows:
                row["content"] = texts.get(row["note_id"], "")
    if not rows and not episteme.data.engine.fetch_one("SELECT 1 AS x FROM anki_cards LIMIT 1"):
        errors.print("[yellow]No Anki data in the database yet: run `episteme sync anki`.[/]")
    columns = ["card_id", "deck_name", "state", "due_date", "interval_days", "lapses", "tags"] + (["content"] if show_content else [])
    emit(state, rows, table(f"{len(rows)} card(s)", columns, [[row[c] for c in columns] for row in rows]))


def checks(episteme: Episteme) -> list[CheckResult]:
    if not episteme.anki.is_configured:
        return [CheckResult(name="anki: collection", ok=True, detail="not configured (ANKI__COLLECTION_PATH), skipped by 'sync'")]
    try:
        return [CheckResult(name="anki: collection", ok=True, detail=str(episteme.anki.path))]
    except AnkiCollectionNotFoundError as error:
        return [CheckResult(name="anki: collection", ok=False, detail=str(error))]


extension = Extension(name="anki", app=app, help="The Anki collection.", checks=(checks,), order=30)
