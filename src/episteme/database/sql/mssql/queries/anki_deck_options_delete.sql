-- $ids is a list of keys, sent as JSON.
DELETE FROM [{schema}].anki_deck_options WHERE options_id IN (SELECT j.id FROM OPENJSON($ids) WITH (id BIGINT '$') AS j)
