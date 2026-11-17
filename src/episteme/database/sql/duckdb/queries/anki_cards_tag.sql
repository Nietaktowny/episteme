-- A condition: the note has the tag $tag, or a tag below it ('lang' matches 'lang::pl'), in any case.
AND len(list_filter(tags, lambda t: lower(t) = lower($tag) OR starts_with(lower(t), lower($tag) || '::'))) > 0
