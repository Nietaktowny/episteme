-- The cards of anki_card_details that meet the conditions put at the marker in the WHERE (each one starts with AND).
-- tags is JSON text on SQL Server (it has no list type).
SELECT card_id, note_id, deck_name, state, due_date, interval_days, lapses, tags
FROM [{schema}].anki_card_details
WHERE 1 = 1 {conditions}
ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, card_id
