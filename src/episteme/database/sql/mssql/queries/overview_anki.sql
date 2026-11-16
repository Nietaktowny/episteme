SELECT
    (SELECT COUNT(*) FROM [{schema}].anki_notes)                            AS notes,
    (SELECT COUNT(*) FROM [{schema}].anki_decks)                            AS decks,
    (SELECT COUNT(*) FROM [{schema}].anki_reviews)                          AS reviews,
    COUNT(*)                                                     AS cards,
    COUNT(CASE WHEN is_due = 1 THEN 1 END)                       AS due,
    COUNT(CASE WHEN maturity = N'new' THEN 1 END)                AS new,
    COUNT(CASE WHEN is_suspended = 1 THEN 1 END)                 AS suspended,
    COUNT(CASE WHEN is_leech = 1 THEN 1 END)                     AS leeches
FROM [{schema}].anki_card_details
