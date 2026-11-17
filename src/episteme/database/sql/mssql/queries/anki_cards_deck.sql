-- A condition: the card is in the deck $deck or in a deck below it.
AND (deck_name = $deck OR LEFT(deck_name, LEN(CAST($deck AS NVARCHAR(450))) + 2) = $deck + N'::')
