-- A condition: the note has the tag $tag, or a tag below it ('lang' matches 'lang::pl'), in any case.
AND EXISTS (SELECT 1 FROM OPENJSON(tags) AS j WHERE LOWER(j.value) = LOWER($tag) OR LOWER(j.value) LIKE LOWER($tag) + N'::%')
