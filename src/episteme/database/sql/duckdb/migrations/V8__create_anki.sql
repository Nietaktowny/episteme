-- Statistics and scheduling from an Anki collection: a derived copy, rebuilt by every Anki sync.
-- Anki's collection is the only source of truth; nothing writes here except the sync.
--
-- Nothing here is the content of a card: there is no column for the text of a note, a card or a
-- template. A note is its type and tags, a card its scheduling state, a review one answer given.
--
-- Times are UTC. Days (due_date, review_day) are Anki days: the day in the configured time zone,
-- starting at the rollover hour (4 a.m. by default) instead of at midnight.

-- One row (id = 1): the collection as a whole.
CREATE TABLE anki_collection (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version    INTEGER NOT NULL,
    scheduler_version INTEGER NOT NULL,
    created_date      DATE NOT NULL,
    rollover_hour     INTEGER NOT NULL,
    fsrs_enabled      BOOLEAN NOT NULL,
    modified_at       TIMESTAMP NOT NULL,
    synced_at         TIMESTAMP NOT NULL
);

-- One row per deck. name has '::' between the levels; options_id is the preset the deck uses (NULL for a
-- filtered deck); new_limit, review_limit and desired_retention are the deck's own overrides of the preset.
CREATE TABLE anki_decks (
    deck_id           BIGINT PRIMARY KEY,
    name              TEXT NOT NULL,
    parent_name       TEXT,
    is_filtered       BOOLEAN NOT NULL,
    options_id        BIGINT,
    new_limit         INTEGER,
    review_limit      INTEGER,
    desired_retention DOUBLE
);

-- One row per options preset: the scheduling options decks share. Steps are in minutes and intervals in days;
-- ease is in percent. fsrs_params are the parameters FSRS was optimized to (empty if it never was).
CREATE TABLE anki_deck_options (
    options_id                    BIGINT PRIMARY KEY,
    name                          TEXT NOT NULL,
    modified_at                   TIMESTAMP,
    new_per_day                   INTEGER NOT NULL,
    reviews_per_day               INTEGER NOT NULL,
    new_per_day_minimum           INTEGER NOT NULL,
    learn_steps_minutes           DOUBLE[] NOT NULL,
    relearn_steps_minutes         DOUBLE[] NOT NULL,
    graduating_interval_good_days INTEGER NOT NULL,
    graduating_interval_easy_days INTEGER NOT NULL,
    initial_ease_percent          INTEGER NOT NULL,
    easy_multiplier               DOUBLE NOT NULL,
    hard_multiplier               DOUBLE NOT NULL,
    lapse_multiplier              DOUBLE NOT NULL,
    interval_multiplier           DOUBLE NOT NULL,
    maximum_interval_days         INTEGER NOT NULL,
    minimum_lapse_interval_days   INTEGER NOT NULL,
    leech_threshold               INTEGER NOT NULL,
    leech_action                  TEXT,
    bury_new                      BOOLEAN NOT NULL,
    bury_reviews                  BOOLEAN NOT NULL,
    bury_interday_learning        BOOLEAN NOT NULL,
    new_card_insert_order         TEXT,
    new_card_gather_priority      TEXT,
    new_card_sort_order           TEXT,
    review_order                  TEXT,
    new_mix                       TEXT,
    interday_learning_mix         TEXT,
    desired_retention             DOUBLE,
    historical_retention          DOUBLE,
    fsrs_params                   DOUBLE[] NOT NULL,
    fsrs_params_version           INTEGER,
    easy_days_percentages         DOUBLE[] NOT NULL,
    ignore_revlogs_before         TEXT
);

-- One row per note: its type and tags, not its fields.
CREATE TABLE anki_notes (
    note_id        BIGINT PRIMARY KEY,
    note_type_id   BIGINT NOT NULL,
    note_type_name TEXT NOT NULL,
    tags           TEXT[] NOT NULL,
    created_at     TIMESTAMP NOT NULL,
    modified_at    TIMESTAMP NOT NULL
);

-- One row per card: its scheduling state. state is new, learning, review or relearning. deck_id is the real deck,
-- also for a card that is borrowed by a filtered deck. due_position is the place of a new card in the queue; due_date
-- is the day any other card is due (a day of Anki, see above); due_at is the moment a card in intra-day learning is
-- due. stability_days, difficulty, desired_retention, decay and last_review_at are the FSRS memory state (NULL for
-- cards it has not scored). ease_percent is the SM-2 ease factor, which FSRS does not use.
CREATE TABLE anki_cards (
    card_id           BIGINT PRIMARY KEY,
    note_id           BIGINT NOT NULL,
    deck_id           BIGINT NOT NULL,
    in_filtered_deck  BOOLEAN NOT NULL,
    template_ordinal  INTEGER NOT NULL,
    created_at        TIMESTAMP NOT NULL,
    modified_at       TIMESTAMP NOT NULL,
    state             TEXT NOT NULL,
    is_suspended      BOOLEAN NOT NULL,
    is_buried         BOOLEAN NOT NULL,
    due_position      INTEGER,
    due_date          DATE,
    due_at            TIMESTAMP,
    interval_days     INTEGER NOT NULL,
    ease_percent      INTEGER,
    reviews           INTEGER NOT NULL,
    lapses            INTEGER NOT NULL,
    flag              INTEGER NOT NULL,
    stability_days    DOUBLE,
    difficulty        DOUBLE,
    desired_retention DOUBLE,
    decay             DOUBLE,
    last_review_at    TIMESTAMP
);
CREATE INDEX idx_anki_cards_note_id ON anki_cards (note_id);
CREATE INDEX idx_anki_cards_deck_id ON anki_cards (deck_id);

-- One row per entry of the review history, which only grows. review_id is the time of the review in milliseconds
-- since 1970. rating is 1 (Again) to 4 (Easy), NULL for a manual change; kind is learning, review, relearning,
-- filtered, manual or rescheduled. A card that was deleted keeps its reviews.
CREATE TABLE anki_reviews (
    review_id              BIGINT PRIMARY KEY,
    card_id                BIGINT NOT NULL,
    reviewed_at            TIMESTAMP NOT NULL,
    review_day             DATE NOT NULL,
    rating                 INTEGER,
    kind                   TEXT NOT NULL,
    interval_days          DOUBLE NOT NULL,
    previous_interval_days DOUBLE NOT NULL,
    duration_seconds       DOUBLE NOT NULL
);
CREATE INDEX idx_anki_reviews_card_id ON anki_reviews (card_id);
CREATE INDEX idx_anki_reviews_review_day ON anki_reviews (review_day);
