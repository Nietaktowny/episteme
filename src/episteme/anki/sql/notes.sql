-- What a note is, never what it says: its type and its tags.
-- The note's fields (flds, sfld) are deliberately not selected, here or anywhere else.
SELECT n.id, n.mid, t.name AS note_type_name, n.mod, n.tags
FROM notes AS n
LEFT JOIN notetypes AS t ON t.id = n.mid
ORDER BY n.id
