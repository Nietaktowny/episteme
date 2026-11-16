-- A card's scheduling state, never its content. The card's text lives in its note's fields, which are not selected.
-- A card in a filtered deck keeps its real due in odue (for new and review cards); its deck is odid.
SELECT
    id, nid, CASE WHEN odid != 0 THEN odid ELSE did END AS did, odid != 0 AS in_filtered_deck,
    ord, mod, type, queue,
    CASE WHEN odid != 0 AND type IN (0, 2) THEN odue ELSE due END AS due,
    ivl, factor, reps, lapses, flags, data
FROM cards
ORDER BY id
