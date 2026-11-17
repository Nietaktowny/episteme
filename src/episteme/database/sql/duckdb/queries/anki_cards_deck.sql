-- A condition: the card is in the deck $deck or in a deck below it.
AND (deck_name = $deck OR starts_with(deck_name, $deck || '::'))
