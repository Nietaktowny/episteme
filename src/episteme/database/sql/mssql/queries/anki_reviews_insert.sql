-- $rows is a list of AnkiReview rows, sent as JSON; one statement stores them all.
INSERT INTO [{schema}].anki_reviews (review_id, card_id, reviewed_at, review_day, rating, kind, interval_days, previous_interval_days, duration_seconds)
SELECT review_id, card_id, reviewed_at, review_day, rating, kind, interval_days, previous_interval_days, duration_seconds
FROM OPENJSON($rows) WITH (
        review_id BIGINT '$.review_id',
        card_id BIGINT '$.card_id',
        reviewed_at DATETIME2(6) '$.reviewed_at',
        review_day DATE '$.review_day',
        rating INT '$.rating',
        kind NVARCHAR(50) '$.kind',
        interval_days FLOAT '$.interval_days',
        previous_interval_days FLOAT '$.previous_interval_days',
        duration_seconds FLOAT '$.duration_seconds'
);
