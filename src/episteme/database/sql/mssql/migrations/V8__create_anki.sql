-- Statistics and scheduling from an Anki collection: a derived copy, rebuilt by every Anki sync.
-- Anki's collection is the only source of truth; nothing writes here except the sync.
--
-- Nothing here is the content of a card: there is no column for the text of a note, a card or a
-- template. A note is its type and tags, a card its scheduling state, a review one answer given.
--
-- Times are UTC. Days (due_date, review_day) are Anki days: the day in the configured time zone,
-- starting at the rollover hour (4 a.m. by default) instead of at midnight.
--
-- SQL Server has no list type: the columns that are lists in the DuckDB schema (tags, steps, FSRS
-- parameters) hold a JSON array as text. OPENJSON(column) turns one back into rows.

-- One row (id = 1): the collection as a whole.
CREATE TABLE [{schema}].anki_collection (
    id                INT          NOT NULL PRIMARY KEY CHECK (id = 1),
    schema_version    INT          NOT NULL,
    scheduler_version INT          NOT NULL,
    created_date      DATE         NOT NULL,
    rollover_hour     INT          NOT NULL,
    fsrs_enabled      BIT          NOT NULL,
    modified_at       DATETIME2(6) NOT NULL,
    synced_at         DATETIME2(6) NOT NULL
);

-- One row per deck. name has '::' between the levels; options_id is the preset the deck uses (NULL for a
-- filtered deck); new_limit, review_limit and desired_retention are the deck's own overrides of the preset.
CREATE TABLE [{schema}].anki_decks (
    deck_id           BIGINT        NOT NULL PRIMARY KEY,
    name              NVARCHAR(450) NOT NULL,
    parent_name       NVARCHAR(450) NULL,
    is_filtered       BIT           NOT NULL,
    options_id        BIGINT        NULL,
    new_limit         INT           NULL,
    review_limit      INT           NULL,
    desired_retention FLOAT         NULL
);

-- One row per options preset: the scheduling options decks share. Steps are in minutes and intervals in days;
-- ease is in percent. fsrs_params are the parameters FSRS was optimized to (empty if it never was).
CREATE TABLE [{schema}].anki_deck_options (
    options_id                    BIGINT        NOT NULL PRIMARY KEY,
    name                          NVARCHAR(450) NOT NULL,
    modified_at                   DATETIME2(6)  NULL,
    new_per_day                   INT           NOT NULL,
    reviews_per_day               INT           NOT NULL,
    new_per_day_minimum           INT           NOT NULL,
    learn_steps_minutes           NVARCHAR(MAX) NOT NULL CHECK (ISJSON(learn_steps_minutes) = 1),
    relearn_steps_minutes         NVARCHAR(MAX) NOT NULL CHECK (ISJSON(relearn_steps_minutes) = 1),
    graduating_interval_good_days INT           NOT NULL,
    graduating_interval_easy_days INT           NOT NULL,
    initial_ease_percent          INT           NOT NULL,
    easy_multiplier               FLOAT         NOT NULL,
    hard_multiplier               FLOAT         NOT NULL,
    lapse_multiplier              FLOAT         NOT NULL,
    interval_multiplier           FLOAT         NOT NULL,
    maximum_interval_days         INT           NOT NULL,
    minimum_lapse_interval_days   INT           NOT NULL,
    leech_threshold               INT           NOT NULL,
    leech_action                  NVARCHAR(100) NULL,
    bury_new                      BIT           NOT NULL,
    bury_reviews                  BIT           NOT NULL,
    bury_interday_learning        BIT           NOT NULL,
    new_card_insert_order         NVARCHAR(100) NULL,
    new_card_gather_priority      NVARCHAR(100) NULL,
    new_card_sort_order           NVARCHAR(100) NULL,
    review_order                  NVARCHAR(100) NULL,
    new_mix                       NVARCHAR(100) NULL,
    interday_learning_mix         NVARCHAR(100) NULL,
    desired_retention             FLOAT         NULL,
    historical_retention          FLOAT         NULL,
    fsrs_params                   NVARCHAR(MAX) NOT NULL CHECK (ISJSON(fsrs_params) = 1),
    fsrs_params_version           INT           NULL,
    easy_days_percentages         NVARCHAR(MAX) NOT NULL CHECK (ISJSON(easy_days_percentages) = 1),
    ignore_revlogs_before         NVARCHAR(100) NULL
);

-- One row per note: its type and tags, not its fields.
CREATE TABLE [{schema}].anki_notes (
    note_id        BIGINT        NOT NULL PRIMARY KEY,
    note_type_id   BIGINT        NOT NULL,
    note_type_name NVARCHAR(450) NOT NULL,
    tags           NVARCHAR(MAX) NOT NULL CHECK (ISJSON(tags) = 1),
    created_at     DATETIME2(6)  NOT NULL,
    modified_at    DATETIME2(6)  NOT NULL
);

-- One row per card: its scheduling state. state is new, learning, review or relearning. deck_id is the real deck,
-- also for a card that is borrowed by a filtered deck. due_position is the place of a new card in the queue; due_date
-- is the day any other card is due (a day of Anki, see above); due_at is the moment a card in intra-day learning is
-- due. stability_days, difficulty, desired_retention, decay and last_review_at are the FSRS memory state (NULL for
-- cards it has not scored). ease_percent is the SM-2 ease factor, which FSRS does not use.
CREATE TABLE [{schema}].anki_cards (
    card_id           BIGINT       NOT NULL PRIMARY KEY,
    note_id           BIGINT       NOT NULL,
    deck_id           BIGINT       NOT NULL,
    in_filtered_deck  BIT          NOT NULL,
    template_ordinal  INT          NOT NULL,
    created_at        DATETIME2(6) NOT NULL,
    modified_at       DATETIME2(6) NOT NULL,
    state             NVARCHAR(50) NOT NULL,
    is_suspended      BIT          NOT NULL,
    is_buried         BIT          NOT NULL,
    due_position      INT          NULL,
    due_date          DATE         NULL,
    due_at            DATETIME2(6) NULL,
    interval_days     INT          NOT NULL,
    ease_percent      INT          NULL,
    reviews           INT          NOT NULL,
    lapses            INT          NOT NULL,
    flag              INT          NOT NULL,
    stability_days    FLOAT        NULL,
    difficulty        FLOAT        NULL,
    desired_retention FLOAT        NULL,
    decay             FLOAT        NULL,
    last_review_at    DATETIME2(6) NULL
);
GO
CREATE INDEX idx_anki_cards_note_id ON [{schema}].anki_cards (note_id);
CREATE INDEX idx_anki_cards_deck_id ON [{schema}].anki_cards (deck_id);
GO

-- One row per entry of the review history, which only grows. review_id is the time of the review in milliseconds
-- since 1970. rating is 1 (Again) to 4 (Easy), NULL for a manual change; kind is learning, review, relearning,
-- filtered, manual or rescheduled. A card that was deleted keeps its reviews.
CREATE TABLE [{schema}].anki_reviews (
    review_id              BIGINT       NOT NULL PRIMARY KEY,
    card_id                BIGINT       NOT NULL,
    reviewed_at            DATETIME2(6) NOT NULL,
    review_day             DATE         NOT NULL,
    rating                 INT          NULL,
    kind                   NVARCHAR(50) NOT NULL,
    interval_days          FLOAT        NOT NULL,
    previous_interval_days FLOAT        NOT NULL,
    duration_seconds       FLOAT        NOT NULL
);
GO
CREATE INDEX idx_anki_reviews_card_id ON [{schema}].anki_reviews (card_id);
CREATE INDEX idx_anki_reviews_review_day ON [{schema}].anki_reviews (review_day);
