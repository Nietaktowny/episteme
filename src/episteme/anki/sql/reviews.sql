-- The review history after a given entry (its id is the time of the review in milliseconds), oldest first.
SELECT id, cid, ease, ivl, lastIvl, time, type FROM revlog WHERE id > ? ORDER BY id
