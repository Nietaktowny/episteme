SELECT
    card_id, note_id, deck_id, in_filtered_deck, template_ordinal, created_at, modified_at, state, is_suspended,
    is_buried, due_position, due_date, due_at, interval_days, ease_percent, reviews, lapses, flag, stability_days,
    difficulty, desired_retention, decay, last_review_at
FROM anki_cards
ORDER BY card_id
