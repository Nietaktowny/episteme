-- $rows is a list of cards (see AnkiCard); one statement stores them all.
INSERT INTO anki_cards
SELECT
    r.card_id::BIGINT, r.note_id::BIGINT, r.deck_id::BIGINT, r.in_filtered_deck::BOOLEAN, r.template_ordinal::INTEGER,
    r.created_at::TIMESTAMP, r.modified_at::TIMESTAMP, r.state::TEXT, r.is_suspended::BOOLEAN, r.is_buried::BOOLEAN,
    r.due_position::INTEGER, r.due_date::DATE, r.due_at::TIMESTAMP, r.interval_days::INTEGER, r.ease_percent::INTEGER,
    r.reviews::INTEGER, r.lapses::INTEGER, r.flag::INTEGER, r.stability_days::DOUBLE, r.difficulty::DOUBLE,
    r.desired_retention::DOUBLE, r.decay::DOUBLE, r.last_review_at::TIMESTAMP
FROM unnest($rows) AS t(r)
ON CONFLICT (card_id) DO UPDATE SET
    note_id = excluded.note_id,
    deck_id = excluded.deck_id,
    in_filtered_deck = excluded.in_filtered_deck,
    template_ordinal = excluded.template_ordinal,
    created_at = excluded.created_at,
    modified_at = excluded.modified_at,
    state = excluded.state,
    is_suspended = excluded.is_suspended,
    is_buried = excluded.is_buried,
    due_position = excluded.due_position,
    due_date = excluded.due_date,
    due_at = excluded.due_at,
    interval_days = excluded.interval_days,
    ease_percent = excluded.ease_percent,
    reviews = excluded.reviews,
    lapses = excluded.lapses,
    flag = excluded.flag,
    stability_days = excluded.stability_days,
    difficulty = excluded.difficulty,
    desired_retention = excluded.desired_retention,
    decay = excluded.decay,
    last_review_at = excluded.last_review_at
