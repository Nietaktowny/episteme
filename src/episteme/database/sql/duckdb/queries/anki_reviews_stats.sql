-- How many history entries are stored and the id of the newest: where an incremental sync continues from.
SELECT count(*) AS count, max(review_id) AS newest FROM anki_reviews
