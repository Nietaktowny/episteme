-- $rows is a list of notes (see AnkiNote); one statement stores them all.
INSERT INTO anki_notes
SELECT
    r.note_id::BIGINT, r.note_type_id::BIGINT, r.note_type_name::TEXT, r.tags::TEXT[],
    r.created_at::TIMESTAMP, r.modified_at::TIMESTAMP
FROM unnest($rows) AS t(r)
ON CONFLICT (note_id) DO UPDATE SET
    note_type_id = excluded.note_type_id,
    note_type_name = excluded.note_type_name,
    tags = excluded.tags,
    created_at = excluded.created_at,
    modified_at = excluded.modified_at
