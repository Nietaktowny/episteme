SELECT note_id, note_type_id, note_type_name, tags, created_at, modified_at
FROM [{schema}].anki_notes
ORDER BY note_id
