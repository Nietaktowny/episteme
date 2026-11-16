-- How many history entries have an id up to the given one.
SELECT count(*) FROM revlog WHERE id <= ?
