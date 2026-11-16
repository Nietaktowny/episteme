"""Pydantic models of the Anki data Episteme stores: statistics and scheduling, never card content.

Everything here describes *how a card is doing*, not what it says: there is no field for the
text of a note, a card's question or answer, or a template. A note is only its type and tags, a
card its scheduling state, a review one answer given.

All times are naive UTC datetimes and days are dates (the Anki day, which starts at the rollover
hour, not at midnight), so the same collection gives the same data on every machine.

:class:`AnkiSource` is what :meth:`episteme.database.Database.sync_anki` reads from; the reader
that implements it is :class:`episteme.anki.AnkiSnapshot`.
"""

from collections.abc import Iterator
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CardState(StrEnum):
    """Where a card is in its life.

    Examples:
        >>> CardState("review")
        <CardState.REVIEW: 'review'>
    """

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


class ReviewKind(StrEnum):
    """What kind of entry a line of the review history is.

    ``manual`` and ``rescheduled`` are not answers given by the user (a card set to a due date, or
    moved by an add-on), so they have no rating and no time.

    Examples:
        >>> ReviewKind("filtered")
        <ReviewKind.FILTERED: 'filtered'>
    """

    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"
    FILTERED = "filtered"
    MANUAL = "manual"
    RESCHEDULED = "rescheduled"
    OTHER = "other"


class AnkiModel(BaseModel):
    """Base of the Anki models: immutable, enums as their values, so a model dumps to what is stored."""

    model_config = ConfigDict(frozen=True, use_enum_values=True)


class AnkiCollectionInfo(AnkiModel):
    """The collection as a whole.

    Attributes:
        schema_version: Version of the collection's database schema (18 for Anki 2.1.50 and later).
        scheduler_version: Version of the scheduler the collection uses (2 is the one Anki 2.1+ has).
        created_date: Day the collection was created: day 0 of every card's due day.
        rollover_hour: Hour of the day at which a new Anki day starts (4 by default).
        fsrs_enabled: Whether the FSRS scheduler is switched on.
        modified_at: When the collection was last modified.

    Examples:
        >>> AnkiCollectionInfo(
        ...     schema_version=18, scheduler_version=2, created_date=date(2021, 10, 19),
        ...     rollover_hour=4, fsrs_enabled=True, modified_at=datetime(2026, 11, 16, 12, 0),
        ... ).rollover_hour
        4
    """

    schema_version: int = Field(..., description="Version of the collection's database schema")
    scheduler_version: int = Field(..., description="Version of the scheduler the collection uses")
    created_date: date = Field(..., description="Day the collection was created (due day 0)")
    rollover_hour: int = Field(..., ge=0, le=23, description="Hour at which a new Anki day starts")
    fsrs_enabled: bool = Field(..., description="Whether FSRS is switched on")
    modified_at: datetime = Field(..., description="When the collection was last modified (UTC)")


class AnkiDeck(AnkiModel):
    """A deck: its place in the tree and the scheduling options it uses. Its description is not read.

    Attributes:
        deck_id: Id of the deck.
        name: Full name, with ``::`` between the levels (``Languages::Polish``).
        parent_name: Name of the parent deck, or ``None`` for a top-level deck.
        is_filtered: Whether this is a filtered (custom study) deck, which borrows cards from others.
        options_id: Id of the options preset the deck uses (``None`` for a filtered deck).
        new_limit: This deck's own limit of new cards a day, if it overrides the preset's.
        review_limit: This deck's own limit of reviews a day, if it overrides the preset's.
        desired_retention: This deck's own desired retention (FSRS), if it overrides the preset's.

    Examples:
        >>> AnkiDeck(deck_id=1, name="Languages::Polish", parent_name="Languages", is_filtered=False, options_id=1).name
        'Languages::Polish'
    """

    deck_id: int
    name: str
    parent_name: str | None = None
    is_filtered: bool = False
    options_id: int | None = None
    new_limit: int | None = None
    review_limit: int | None = None
    desired_retention: float | None = None


class AnkiDeckOptions(AnkiModel):
    """An options preset: the scheduling options that decks share.

    Steps are in minutes. ``fsrs_params`` are the parameters FSRS was optimized to, empty if it
    never was. Intervals are in days.

    Examples:
        >>> AnkiDeckOptions(options_id=1, name="Default", new_per_day=20, reviews_per_day=200).learn_steps_minutes
        []
    """

    options_id: int
    name: str
    modified_at: datetime | None = None
    new_per_day: int = 0
    reviews_per_day: int = 0
    new_per_day_minimum: int = 0
    learn_steps_minutes: list[float] = Field(default_factory=list)
    relearn_steps_minutes: list[float] = Field(default_factory=list)
    graduating_interval_good_days: int = 0
    graduating_interval_easy_days: int = 0
    initial_ease_percent: int = 0
    easy_multiplier: float = 0.0
    hard_multiplier: float = 0.0
    lapse_multiplier: float = 0.0
    interval_multiplier: float = 0.0
    maximum_interval_days: int = 0
    minimum_lapse_interval_days: int = 0
    leech_threshold: int = 0
    leech_action: str | None = None
    bury_new: bool = False
    bury_reviews: bool = False
    bury_interday_learning: bool = False
    new_card_insert_order: str | None = None
    new_card_gather_priority: str | None = None
    new_card_sort_order: str | None = None
    review_order: str | None = None
    new_mix: str | None = None
    interday_learning_mix: str | None = None
    desired_retention: float | None = None
    historical_retention: float | None = None
    fsrs_params: list[float] = Field(default_factory=list)
    fsrs_params_version: int | None = None
    easy_days_percentages: list[float] = Field(default_factory=list)
    ignore_revlogs_before: str | None = None


class AnkiNote(AnkiModel):
    """A note, without its fields: only what type it is and how it is tagged.

    Attributes:
        note_id: Id of the note; this is what the cards refer to.
        note_type_id: Id of the note type.
        note_type_name: Name of the note type (``Basic``, ``Cloze``).
        tags: The tags of the note, in the order Anki has them.
        created_at: When the note was created.
        modified_at: When the note was last modified.

    Examples:
        >>> AnkiNote(note_id=1, note_type_id=2, note_type_name="Cloze", tags=["ai::attention"],
        ...          created_at=datetime(2026, 1, 1), modified_at=datetime(2026, 1, 2)).tags
        ['ai::attention']
    """

    note_id: int
    note_type_id: int
    note_type_name: str
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    modified_at: datetime


class AnkiNoteContent(AnkiModel):
    """What a note says: the text of its fields. Read on demand and **never stored in the database**.

    Attributes:
        note_id: Id of the note; the same as :attr:`AnkiNote.note_id`.
        note_type_name: Name of the note type.
        tags: The tags of the note.
        fields: The fields by name, in the order of the note type, as Anki stores them (HTML, with
            ``[sound:...]`` and ``{{c1::cloze}}`` markup).

    Examples:
        >>> content = AnkiNoteContent(note_id=1, note_type_name="Basic", fields={"Front": "2+2", "Back": "4"})
        >>> content.fields["Back"]
        '4'
    """

    note_id: int
    note_type_name: str
    tags: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)


class AnkiCard(AnkiModel):
    """A card's scheduling state.

    Attributes:
        card_id: Id of the card.
        note_id: Id of the note it belongs to.
        deck_id: Id of the deck the card belongs to. For a card that is in a filtered deck
            this is the deck it came from, so statistics stay with the real deck.
        in_filtered_deck: Whether the card is currently in a filtered deck.
        template_ordinal: Which card of its note this is (0 for the first template).
        created_at: When the card was created.
        modified_at: When the card was last changed, for example by an answer.
        state: New, learning, review or relearning.
        is_suspended: Whether the card is suspended.
        is_buried: Whether the card is buried until the next day.
        due_position: Place in the queue of new cards (``None`` for the other cards).
        due_date: Day the card is due (``None`` for new cards).
        due_at: The moment a card in intra-day learning is due (``None`` for the other cards).
        interval_days: Current interval in days.
        ease_percent: Ease factor in percent (``250`` is 2.5), ``None`` for new cards.
        reviews: How many times the card was answered.
        lapses: How many times it was forgotten.
        flag: Colour flag, 0 for none.
        stability_days: FSRS stability: the days after which the chance to remember falls to the desired retention.
        difficulty: FSRS difficulty, from 1 (easy) to 10 (hard).
        desired_retention: Desired retention the card's memory state was last computed with.
        decay: Decay of FSRS's forgetting curve; ``None`` for the older FSRS versions, which had a fixed one.
        last_review_at: When the card was last reviewed, as FSRS knows it.

    Examples:
        >>> card = AnkiCard(card_id=1, note_id=2, deck_id=3, template_ordinal=0, created_at=datetime(2026, 1, 1),
        ...                 modified_at=datetime(2026, 1, 1), state="new", due_position=7)
        >>> card.state, card.is_suspended
        ('new', False)
    """

    card_id: int
    note_id: int
    deck_id: int
    in_filtered_deck: bool = False
    template_ordinal: int
    created_at: datetime
    modified_at: datetime
    state: CardState
    is_suspended: bool = False
    is_buried: bool = False
    due_position: int | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    interval_days: int = 0
    ease_percent: int | None = None
    reviews: int = 0
    lapses: int = 0
    flag: int = 0
    stability_days: float | None = None
    difficulty: float | None = None
    desired_retention: float | None = None
    decay: float | None = None
    last_review_at: datetime | None = None


class AnkiReview(AnkiModel):
    """One line of the review history: an answer given, or a manual change of a card.

    Attributes:
        review_id: Id of the entry, which is the moment of the review in milliseconds since 1970.
        card_id: The card that was reviewed (it may have been deleted since).
        reviewed_at: When the review happened.
        review_day: The Anki day it belongs to: the day in the configured time zone, shifted by the rollover hour.
        rating: 1 (Again), 2 (Hard), 3 (Good) or 4 (Easy); ``None`` for a manual change.
        kind: What kind of entry it is.
        interval_days: Interval after the review, in days (a fraction for intervals under a day).
        previous_interval_days: Interval before it.
        duration_seconds: Time spent on the answer.

    Examples:
        >>> review = AnkiReview(review_id=1_700_000_000_000, card_id=1, reviewed_at=datetime(2023, 11, 14, 22, 13, 20),
        ...                     review_day=date(2023, 11, 14), rating=3, kind="review", interval_days=12,
        ...                     previous_interval_days=5, duration_seconds=4.5)
        >>> review.rating, review.kind
        (3, 'review')
    """

    review_id: int
    card_id: int
    reviewed_at: datetime
    review_day: date
    rating: int | None = Field(None, ge=1, le=4)
    kind: ReviewKind
    interval_days: float = 0.0
    previous_interval_days: float = 0.0
    duration_seconds: float = 0.0


class AnkiSource(Protocol):
    """What the database reads an Anki sync from; implemented by :class:`episteme.anki.AnkiSnapshot`.

    The database never learns where the data comes from: the reader hides the collection's file
    format. Notes, cards and decks are read whole; the review history, which is large and only
    grows, can be read from a point on.
    """

    def info(self) -> AnkiCollectionInfo:
        """The collection as a whole."""
        ...

    def decks(self) -> list[AnkiDeck]:
        """Every deck."""
        ...

    def deck_options(self) -> list[AnkiDeckOptions]:
        """Every options preset."""
        ...

    def notes(self) -> Iterator[AnkiNote]:
        """Every note, without its fields."""
        ...

    def cards(self) -> Iterator[AnkiCard]:
        """Every card's scheduling state."""
        ...

    def count_reviews(self, up_to: int | None = None) -> int:
        """How many history entries there are, or only those with an id up to ``up_to``."""
        ...

    def reviews(self, after: int | None = None) -> Iterator[AnkiReview]:
        """The history entries with an id greater than ``after`` (all of them if ``None``), oldest first."""
        ...
