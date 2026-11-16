-- How many history entries are stored and the id of the newest: where an incremental sync continues from.
SELECT COUNT(*) AS count, MAX(review_id) AS newest FROM [{schema}].anki_reviews
