SELECT
    (SELECT count(*) FROM anki_notes)                        AS notes,
    (SELECT count(*) FROM anki_decks)                        AS decks,
    (SELECT count(*) FROM anki_reviews)                      AS reviews,
    count(*)                                                 AS cards,
    count(*) FILTER (WHERE is_due)                           AS due,
    count(*) FILTER (WHERE maturity = 'new')                 AS new,
    count(*) FILTER (WHERE is_suspended)                     AS suspended,
    count(*) FILTER (WHERE is_leech)                         AS leeches
FROM anki_card_details
