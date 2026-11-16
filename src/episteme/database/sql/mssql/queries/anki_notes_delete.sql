-- $ids is a list of keys, sent as JSON.
DELETE FROM [{schema}].anki_notes WHERE note_id IN (SELECT j.id FROM OPENJSON($ids) WITH (id BIGINT '$') AS j)
