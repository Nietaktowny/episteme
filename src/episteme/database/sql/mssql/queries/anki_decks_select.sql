SELECT deck_id, name, parent_name, is_filtered, options_id, new_limit, review_limit, desired_retention
FROM [{schema}].anki_decks
ORDER BY deck_id
