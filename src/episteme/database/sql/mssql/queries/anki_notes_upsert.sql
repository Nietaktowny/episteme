-- $rows is a list of AnkiNote rows, sent as JSON; one statement stores them all.
MERGE [{schema}].anki_notes WITH (HOLDLOCK) AS target
USING (
    SELECT * FROM OPENJSON($rows) WITH (
        note_id BIGINT '$.note_id',
        note_type_id BIGINT '$.note_type_id',
        note_type_name NVARCHAR(450) '$.note_type_name',
        tags NVARCHAR(MAX) '$.tags' AS JSON,
        created_at DATETIME2(6) '$.created_at',
        modified_at DATETIME2(6) '$.modified_at'
    )
) AS source ON target.note_id = source.note_id
WHEN MATCHED THEN UPDATE SET
    note_type_id = source.note_type_id,
    note_type_name = source.note_type_name,
    tags = source.tags,
    created_at = source.created_at,
    modified_at = source.modified_at
WHEN NOT MATCHED THEN INSERT (note_id, note_type_id, note_type_name, tags, created_at, modified_at)
VALUES (source.note_id, source.note_type_id, source.note_type_name, source.tags, source.created_at, source.modified_at);
