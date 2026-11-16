-- Views over the Anki statistics (tables anki_*, see V8). They are read-only, carry no card content, and
-- every name starts with anki_ so that they are easy to tell from the views over the vault.
--
-- The Anki day starts at the collection's rollover hour (4 a.m. by default), not at midnight, so "today" is
-- anki_today and not current_date. Cards of a filtered deck count towards the deck they belong to.

CREATE VIEW anki_today AS
SELECT (current_localtimestamp() - to_hours(rollover_hour))::DATE AS today
FROM anki_collection;
COMMENT ON VIEW anki_today IS 'The current Anki day, which starts at the rollover hour instead of at midnight: one row (none before the first Anki sync).';

CREATE VIEW anki_note_tags AS
SELECT note_id, unnest(tags) AS tag
FROM anki_notes;
COMMENT ON VIEW anki_note_tags IS 'One row per note and tag.';

CREATE VIEW anki_card_details AS
WITH clock AS (
    SELECT today, (current_timestamp AT TIME ZONE 'UTC') AS now_utc FROM anki_today
),
elapsed AS (
    -- Days since the card was last reviewed: from FSRS's record of it if there is one, else from the due date and interval.
    SELECT
        c.*,
        clock.today,
        greatest(0, CASE WHEN c.last_review_at IS NOT NULL
                         THEN date_diff('second', c.last_review_at, clock.now_utc) / 86400.0
                         ELSE date_diff('day', c.due_date - c.interval_days, clock.today) END) AS elapsed_days
    FROM anki_cards AS c
    CROSS JOIN clock
)
SELECT
    e.card_id, e.note_id, e.deck_id,
    d.name                                                              AS deck_name,
    n.note_type_name, n.tags,
    e.template_ordinal, e.state, e.is_suspended, e.is_buried, e.in_filtered_deck,
    CASE WHEN e.state = 'new' THEN 'new'
         WHEN e.interval_days >= 21 THEN 'mature'
         ELSE 'young' END                                               AS maturity,
    e.due_position, e.due_date, e.due_at,
    e.state <> 'new' AND NOT e.is_suspended AND NOT e.is_buried AND e.due_date <= e.today AS is_due,
    CASE WHEN e.state <> 'new' AND NOT e.is_suspended AND NOT e.is_buried AND e.due_date < e.today
         THEN date_diff('day', e.due_date, e.today) END                 AS days_overdue,
    e.interval_days, e.ease_percent, e.reviews, e.lapses, e.flag,
    COALESCE(len(list_filter(n.tags, lambda tag: lower(tag) = 'leech')) > 0, FALSE) AS is_leech,
    e.stability_days, e.difficulty, e.desired_retention, e.last_review_at,
    -- The chance to recall the card now, by FSRS's forgetting curve (the decay of older FSRS versions is 0.5).
    CASE WHEN e.stability_days > 0
         THEN power(1 + (power(0.9, -1.0 / COALESCE(e.decay, 0.5)) - 1) * e.elapsed_days / e.stability_days,
                    -COALESCE(e.decay, 0.5)) END                        AS retrievability,
    e.created_at, e.modified_at
FROM elapsed AS e
LEFT JOIN anki_decks AS d ON d.deck_id = e.deck_id
LEFT JOIN anki_notes AS n ON n.note_id = e.note_id;
COMMENT ON VIEW anki_card_details IS 'Every card with its deck_name, note_type_name and tags, its maturity (new, young under 21 days, mature), is_due, days_overdue, is_leech (tagged leech) and the FSRS retrievability (the chance to recall it now).';

CREATE VIEW anki_due_forecast AS
SELECT
    greatest(c.due_date, t.today)                                       AS day,
    count(*)                                                            AS cards,
    count(*) FILTER (WHERE c.state = 'review')                          AS reviews,
    count(*) FILTER (WHERE c.state IN ('learning', 'relearning'))       AS learning,
    count(*) FILTER (WHERE c.due_date < t.today)                        AS overdue
FROM anki_cards AS c
CROSS JOIN anki_today AS t
WHERE c.state <> 'new' AND NOT c.is_suspended AND NOT c.is_buried AND c.due_date IS NOT NULL
GROUP BY 1
ORDER BY 1;
COMMENT ON VIEW anki_due_forecast IS 'Cards due per day, as reviews or learning; the overdue ones (also counted in overdue) are due today.';

CREATE VIEW anki_deck_summary AS
SELECT
    d.deck_id,
    d.name                                                              AS deck_name,
    d.is_filtered,
    count(c.card_id)                                                    AS cards,
    count(*) FILTER (WHERE c.maturity = 'new')                          AS new,
    count(*) FILTER (WHERE c.state IN ('learning', 'relearning'))       AS learning,
    count(*) FILTER (WHERE c.state = 'review' AND c.maturity = 'young') AS young,
    count(*) FILTER (WHERE c.state = 'review' AND c.maturity = 'mature') AS mature,
    count(*) FILTER (WHERE c.is_suspended)                              AS suspended,
    count(*) FILTER (WHERE c.is_buried)                                 AS buried,
    count(*) FILTER (WHERE c.is_due)                                    AS due,
    count(*) FILTER (WHERE c.days_overdue > 0)                          AS overdue,
    count(*) FILTER (WHERE c.is_leech)                                  AS leeches,
    COALESCE(sum(c.lapses), 0)::BIGINT                                  AS lapses,
    round(avg(c.interval_days) FILTER (WHERE c.state = 'review'), 1)    AS avg_interval_days,
    round(avg(c.stability_days), 1)                                     AS avg_stability_days,
    round(avg(c.difficulty), 2)                                         AS avg_difficulty
FROM anki_decks AS d
LEFT JOIN anki_card_details AS c ON c.deck_id = d.deck_id
GROUP BY d.deck_id, d.name, d.is_filtered
ORDER BY d.name;
COMMENT ON VIEW anki_deck_summary IS 'Per deck: its cards by state and maturity, how many are suspended, buried, due, overdue or leeches, and the average interval, FSRS stability and difficulty. A deck counts only its own cards, not those of its subdecks.';

CREATE VIEW anki_deck_settings AS
SELECT
    d.deck_id,
    d.name                                                              AS deck_name,
    o.options_id,
    o.name                                                              AS options_name,
    COALESCE(d.new_limit, o.new_per_day)                                AS new_per_day,
    COALESCE(d.review_limit, o.reviews_per_day)                         AS reviews_per_day,
    d.new_limit IS NOT NULL OR d.review_limit IS NOT NULL               AS has_own_limits,
    o.learn_steps_minutes, o.relearn_steps_minutes,
    o.graduating_interval_good_days, o.graduating_interval_easy_days, o.maximum_interval_days,
    o.leech_threshold, o.leech_action,
    o.review_order, o.new_card_gather_priority, o.new_card_sort_order,
    c.fsrs_enabled,
    COALESCE(d.desired_retention, o.desired_retention)                  AS desired_retention,
    len(o.fsrs_params) > 0                                              AS fsrs_optimized
FROM anki_decks AS d
JOIN anki_deck_options AS o ON o.options_id = d.options_id
CROSS JOIN anki_collection AS c
WHERE NOT d.is_filtered
ORDER BY d.name;
COMMENT ON VIEW anki_deck_settings IS 'Per deck, the scheduling options that apply: its options preset, daily limits (the deck''s own if it overrides the preset''s), steps, intervals, leech rules, order, and FSRS desired retention.';

CREATE VIEW anki_daily_reviews AS
SELECT
    review_day                                                          AS day,
    count(*)                                                            AS answers,
    count(*) FILTER (WHERE kind = 'learning')                           AS learning,
    count(*) FILTER (WHERE kind = 'review')                             AS reviewing,
    count(*) FILTER (WHERE kind = 'relearning')                         AS relearning,
    count(*) FILTER (WHERE rating = 1)                                  AS again,
    count(*) FILTER (WHERE rating = 2)                                  AS hard,
    count(*) FILTER (WHERE rating = 3)                                  AS good,
    count(*) FILTER (WHERE rating = 4)                                  AS easy,
    round(sum(duration_seconds) / 60, 1)                                AS minutes,
    round(1 - count(*) FILTER (WHERE kind = 'review' AND rating = 1)::DOUBLE
              / nullif(count(*) FILTER (WHERE kind = 'review'), 0), 4)  AS retention
FROM anki_reviews
WHERE kind IN ('learning', 'review', 'relearning', 'filtered')
GROUP BY review_day
ORDER BY review_day;
COMMENT ON VIEW anki_daily_reviews IS 'Per Anki day: the answers given (by kind and by rating), the minutes spent, and the retention (the share of the answers to reviews, as opposed to learning steps, that were not Again). Manual changes are not counted.';

CREATE VIEW anki_streaks AS
WITH days AS (
    SELECT DISTINCT review_day AS day
    FROM anki_reviews
    WHERE kind IN ('learning', 'review', 'relearning', 'filtered')
),
numbered AS (
    SELECT day, day - CAST(row_number() OVER (ORDER BY day) AS INTEGER) AS streak
    FROM days
)
SELECT
    min(day)                                                            AS start_day,
    max(day)                                                            AS end_day,
    count(*)                                                            AS days,
    max(day) >= (SELECT today FROM anki_today) - 1                      AS is_current
FROM numbered
GROUP BY streak
ORDER BY start_day;
COMMENT ON VIEW anki_streaks IS 'Runs of consecutive Anki days with at least one answer; is_current is true for the run that includes today or yesterday.';

CREATE VIEW anki_deck_retention AS
SELECT
    d.name                                                              AS deck_name,
    count(*)                                                            AS reviews,
    round(1 - avg(CASE WHEN r.rating = 1 THEN 1.0 ELSE 0.0 END), 4)     AS retention,
    count(*) FILTER (WHERE r.review_day > t.today - 30)                 AS reviews_30d,
    round(1 - avg(CASE WHEN r.rating = 1 THEN 1.0 ELSE 0.0 END) FILTER (WHERE r.review_day > t.today - 30), 4) AS retention_30d
FROM anki_reviews AS r
JOIN anki_cards AS c ON c.card_id = r.card_id
JOIN anki_decks AS d ON d.deck_id = c.deck_id
CROSS JOIN anki_today AS t
WHERE r.kind = 'review'
GROUP BY d.name
ORDER BY d.name;
COMMENT ON VIEW anki_deck_retention IS 'Per deck: how many reviews (not learning steps) were answered, and the share that were not Again, over all time and over the last 30 days. Reviews of deleted cards are not counted.';

CREATE VIEW anki_leeches AS
SELECT card_id, note_id, deck_name, tags, state, lapses, interval_days, is_suspended
FROM anki_card_details
WHERE is_leech
ORDER BY lapses DESC, card_id;
COMMENT ON VIEW anki_leeches IS 'Cards tagged leech, the ones with the most lapses first.';

CREATE VIEW anki_tag_summary AS
SELECT
    t.tag,
    count(DISTINCT t.note_id)                                           AS notes,
    count(c.card_id)                                                    AS cards,
    count(*) FILTER (WHERE c.maturity = 'new')                          AS new,
    count(*) FILTER (WHERE c.maturity = 'mature')                       AS mature,
    count(*) FILTER (WHERE c.is_due)                                    AS due,
    count(*) FILTER (WHERE c.is_leech)                                  AS leeches,
    COALESCE(sum(c.lapses), 0)::BIGINT                                  AS lapses,
    round(avg(c.stability_days), 1)                                     AS avg_stability_days
FROM anki_note_tags AS t
LEFT JOIN anki_card_details AS c ON c.note_id = t.note_id
GROUP BY t.tag
ORDER BY t.tag;
COMMENT ON VIEW anki_tag_summary IS 'Per tag: its notes and cards, how many cards are new, mature, due or leeches, the lapses, and the average FSRS stability.';
