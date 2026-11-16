-- $rows is a list of AnkiCard rows, sent as JSON; one statement stores them all.
MERGE [{schema}].anki_cards WITH (HOLDLOCK) AS target
USING (
    SELECT * FROM OPENJSON($rows) WITH (
        card_id BIGINT '$.card_id',
        note_id BIGINT '$.note_id',
        deck_id BIGINT '$.deck_id',
        in_filtered_deck BIT '$.in_filtered_deck',
        template_ordinal INT '$.template_ordinal',
        created_at DATETIME2(6) '$.created_at',
        modified_at DATETIME2(6) '$.modified_at',
        state NVARCHAR(50) '$.state',
        is_suspended BIT '$.is_suspended',
        is_buried BIT '$.is_buried',
        due_position INT '$.due_position',
        due_date DATE '$.due_date',
        due_at DATETIME2(6) '$.due_at',
        interval_days INT '$.interval_days',
        ease_percent INT '$.ease_percent',
        reviews INT '$.reviews',
        lapses INT '$.lapses',
        flag INT '$.flag',
        stability_days FLOAT '$.stability_days',
        difficulty FLOAT '$.difficulty',
        desired_retention FLOAT '$.desired_retention',
        decay FLOAT '$.decay',
        last_review_at DATETIME2(6) '$.last_review_at'
    )
) AS source ON target.card_id = source.card_id
WHEN MATCHED THEN UPDATE SET
    note_id = source.note_id,
    deck_id = source.deck_id,
    in_filtered_deck = source.in_filtered_deck,
    template_ordinal = source.template_ordinal,
    created_at = source.created_at,
    modified_at = source.modified_at,
    state = source.state,
    is_suspended = source.is_suspended,
    is_buried = source.is_buried,
    due_position = source.due_position,
    due_date = source.due_date,
    due_at = source.due_at,
    interval_days = source.interval_days,
    ease_percent = source.ease_percent,
    reviews = source.reviews,
    lapses = source.lapses,
    flag = source.flag,
    stability_days = source.stability_days,
    difficulty = source.difficulty,
    desired_retention = source.desired_retention,
    decay = source.decay,
    last_review_at = source.last_review_at
WHEN NOT MATCHED THEN INSERT (card_id, note_id, deck_id, in_filtered_deck, template_ordinal, created_at, modified_at, state, is_suspended, is_buried, due_position, due_date, due_at, interval_days, ease_percent, reviews, lapses, flag, stability_days, difficulty, desired_retention, decay, last_review_at)
VALUES (source.card_id, source.note_id, source.deck_id, source.in_filtered_deck, source.template_ordinal, source.created_at, source.modified_at, source.state, source.is_suspended, source.is_buried, source.due_position, source.due_date, source.due_at, source.interval_days, source.ease_percent, source.reviews, source.lapses, source.flag, source.stability_days, source.difficulty, source.desired_retention, source.decay, source.last_review_at);
