-- $rows is a list of decks (see AnkiDeck); one statement stores them all.
INSERT INTO anki_decks
SELECT
    r.deck_id::BIGINT, r.name::TEXT, r.parent_name::TEXT, r.is_filtered::BOOLEAN, r.options_id::BIGINT,
    r.new_limit::INTEGER, r.review_limit::INTEGER, r.desired_retention::DOUBLE
FROM unnest($rows) AS t(r)
ON CONFLICT (deck_id) DO UPDATE SET
    name = excluded.name,
    parent_name = excluded.parent_name,
    is_filtered = excluded.is_filtered,
    options_id = excluded.options_id,
    new_limit = excluded.new_limit,
    review_limit = excluded.review_limit,
    desired_retention = excluded.desired_retention
