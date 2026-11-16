-- $rows is a list of history entries (see AnkiReview); one statement stores them all.
INSERT INTO anki_reviews
SELECT
    r.review_id::BIGINT, r.card_id::BIGINT, r.reviewed_at::TIMESTAMP, r.review_day::DATE, r.rating::INTEGER,
    r.kind::TEXT, r.interval_days::DOUBLE, r.previous_interval_days::DOUBLE, r.duration_seconds::DOUBLE
FROM unnest($rows) AS t(r)
