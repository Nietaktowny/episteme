-- A deck's name and its kind (a protobuf: a normal or a filtered deck, with its options id and limits).
-- The deck's description and its study counts are in the blobs that are not selected.
SELECT id, name, kind FROM decks ORDER BY id
