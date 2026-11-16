-- $rows is a list of AnkiDeck rows, sent as JSON; one statement stores them all.
MERGE [{schema}].anki_decks WITH (HOLDLOCK) AS target
USING (
    SELECT * FROM OPENJSON($rows) WITH (
        deck_id BIGINT '$.deck_id',
        name NVARCHAR(450) '$.name',
        parent_name NVARCHAR(450) '$.parent_name',
        is_filtered BIT '$.is_filtered',
        options_id BIGINT '$.options_id',
        new_limit INT '$.new_limit',
        review_limit INT '$.review_limit',
        desired_retention FLOAT '$.desired_retention'
    )
) AS source ON target.deck_id = source.deck_id
WHEN MATCHED THEN UPDATE SET
    name = source.name,
    parent_name = source.parent_name,
    is_filtered = source.is_filtered,
    options_id = source.options_id,
    new_limit = source.new_limit,
    review_limit = source.review_limit,
    desired_retention = source.desired_retention
WHEN NOT MATCHED THEN INSERT (deck_id, name, parent_name, is_filtered, options_id, new_limit, review_limit, desired_retention)
VALUES (source.deck_id, source.name, source.parent_name, source.is_filtered, source.options_id, source.new_limit, source.review_limit, source.desired_retention);
