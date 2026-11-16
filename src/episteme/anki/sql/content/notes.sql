-- THE CONTENT of the notes with the given ids (a JSON list). Only used by the opt-in reading of a note's content;
-- the sync never runs this, which tests guard.
SELECT n.id, n.mid, t.name AS note_type_name, n.tags, n.flds
FROM notes AS n
LEFT JOIN notetypes AS t ON t.id = n.mid
WHERE n.id IN (SELECT value FROM json_each(?))
ORDER BY n.id
