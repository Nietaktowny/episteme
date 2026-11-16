-- Views over the Anki statistics (tables anki_*, see V8). They are read-only, carry no card content, and
-- every name starts with anki_ so that they are easy to tell from the views over the vault.
--
-- The Anki day starts at the collection's rollover hour (4 a.m. by default), not at midnight, so "today" is
-- anki_today and not the calendar date. Cards of a filtered deck count towards the deck they belong to.
-- The clock is UTC (Azure SQL runs in UTC), so for a collection in another time zone the day changes
-- at the rollover hour in UTC, not at the rollover hour there. The lists (tags, steps) are JSON text.

CREATE OR ALTER VIEW [{schema}].anki_today AS
SELECT CAST(DATEADD(hour, -rollover_hour, SYSUTCDATETIME()) AS DATE) AS today
FROM [{schema}].anki_collection;
GO
EXEC [{schema}].episteme_describe_view N'anki_today', N'The current Anki day, which starts at the rollover hour instead of at midnight: one row (none before the first Anki sync).';
GO

CREATE OR ALTER VIEW [{schema}].anki_note_tags AS
SELECT n.note_id, t.value AS tag
FROM [{schema}].anki_notes AS n
CROSS APPLY OPENJSON(n.tags) AS t;
GO
EXEC [{schema}].episteme_describe_view N'anki_note_tags', N'One row per note and tag.';
GO

CREATE OR ALTER VIEW [{schema}].anki_card_details AS
WITH clock AS (
    SELECT today, SYSUTCDATETIME() AS now_utc FROM [{schema}].anki_today
),
elapsed AS (
    -- Days since the card was last reviewed: from FSRS's record of it if there is one, else from the due date and interval.
    SELECT
        c.*,
        clock.today,
        CAST(CASE WHEN c.last_review_at IS NOT NULL
                  THEN CASE WHEN DATEDIFF_BIG(second, c.last_review_at, clock.now_utc) > 0
                            THEN DATEDIFF_BIG(second, c.last_review_at, clock.now_utc) / 86400.0 ELSE 0 END
                  ELSE CASE WHEN DATEDIFF(day, DATEADD(day, -c.interval_days, c.due_date), clock.today) > 0
                            THEN DATEDIFF(day, DATEADD(day, -c.interval_days, c.due_date), clock.today) ELSE 0 END
             END AS FLOAT) AS elapsed_days
    FROM [{schema}].anki_cards AS c
    CROSS JOIN clock
)
SELECT
    e.card_id, e.note_id, e.deck_id,
    d.name                                                              AS deck_name,
    n.note_type_name, n.tags,
    e.template_ordinal, e.state, e.is_suspended, e.is_buried, e.in_filtered_deck,
    CASE WHEN e.state = N'new' THEN N'new'
         WHEN e.interval_days >= 21 THEN N'mature'
         ELSE N'young' END                                              AS maturity,
    e.due_position, e.due_date, e.due_at,
    CAST(CASE WHEN e.state <> N'new' AND e.is_suspended = 0 AND e.is_buried = 0 AND e.due_date <= e.today THEN 1 ELSE 0 END AS BIT) AS is_due,
    CASE WHEN e.state <> N'new' AND e.is_suspended = 0 AND e.is_buried = 0 AND e.due_date < e.today
         THEN DATEDIFF(day, e.due_date, e.today) END                    AS days_overdue,
    e.interval_days, e.ease_percent, e.reviews, e.lapses, e.flag,
    CAST(CASE WHEN EXISTS (SELECT 1 FROM OPENJSON(n.tags) AS t WHERE LOWER(t.value) = N'leech') THEN 1 ELSE 0 END AS BIT) AS is_leech,
    e.stability_days, e.difficulty, e.desired_retention, e.last_review_at,
    -- The chance to recall the card now, by FSRS's forgetting curve (the decay of older FSRS versions is 0.5).
    CASE WHEN e.stability_days > 0
         THEN POWER(1 + (POWER(CAST(0.9 AS FLOAT), -1.0 / COALESCE(e.decay, 0.5)) - 1) * e.elapsed_days / e.stability_days,
                    -COALESCE(e.decay, 0.5)) END                        AS retrievability,
    e.created_at, e.modified_at
FROM elapsed AS e
LEFT JOIN [{schema}].anki_decks AS d ON d.deck_id = e.deck_id
LEFT JOIN [{schema}].anki_notes AS n ON n.note_id = e.note_id;
GO
EXEC [{schema}].episteme_describe_view N'anki_card_details', N'Every card with its deck_name, note_type_name and tags, its maturity (new, young under 21 days, mature), is_due, days_overdue, is_leech (tagged leech) and the FSRS retrievability (the chance to recall it now).';
GO

CREATE OR ALTER VIEW [{schema}].anki_due_forecast AS
SELECT
    d.[day]                                                             AS [day],
    COUNT(*)                                                            AS cards,
    COUNT(CASE WHEN c.state = N'review' THEN 1 END)                     AS reviews,
    COUNT(CASE WHEN c.state IN (N'learning', N'relearning') THEN 1 END) AS learning,
    COUNT(CASE WHEN c.due_date < t.today THEN 1 END)                    AS overdue
FROM [{schema}].anki_cards AS c
CROSS JOIN [{schema}].anki_today AS t
CROSS APPLY (SELECT CASE WHEN c.due_date > t.today THEN c.due_date ELSE t.today END AS [day]) AS d
WHERE c.state <> N'new' AND c.is_suspended = 0 AND c.is_buried = 0 AND c.due_date IS NOT NULL
GROUP BY d.[day];
GO
EXEC [{schema}].episteme_describe_view N'anki_due_forecast', N'Cards due per day, as reviews or learning; the overdue ones (also counted in overdue) are due today.';
GO

CREATE OR ALTER VIEW [{schema}].anki_deck_summary AS
SELECT
    d.deck_id,
    d.name                                                              AS deck_name,
    d.is_filtered,
    COUNT(c.card_id)                                                    AS cards,
    COUNT(CASE WHEN c.maturity = N'new' THEN 1 END)                     AS new,
    COUNT(CASE WHEN c.state IN (N'learning', N'relearning') THEN 1 END) AS learning,
    COUNT(CASE WHEN c.state = N'review' AND c.maturity = N'young' THEN 1 END)  AS young,
    COUNT(CASE WHEN c.state = N'review' AND c.maturity = N'mature' THEN 1 END) AS mature,
    COUNT(CASE WHEN c.is_suspended = 1 THEN 1 END)                      AS suspended,
    COUNT(CASE WHEN c.is_buried = 1 THEN 1 END)                         AS buried,
    COUNT(CASE WHEN c.is_due = 1 THEN 1 END)                            AS due,
    COUNT(CASE WHEN c.days_overdue > 0 THEN 1 END)                      AS overdue,
    COUNT(CASE WHEN c.is_leech = 1 THEN 1 END)                          AS leeches,
    CAST(COALESCE(SUM(CAST(c.lapses AS BIGINT)), 0) AS BIGINT)         AS lapses,
    ROUND(AVG(CASE WHEN c.state = N'review' THEN CAST(c.interval_days AS FLOAT) END), 1) AS avg_interval_days,
    ROUND(AVG(c.stability_days), 1)                                     AS avg_stability_days,
    ROUND(AVG(c.difficulty), 2)                                         AS avg_difficulty
FROM [{schema}].anki_decks AS d
LEFT JOIN [{schema}].anki_card_details AS c ON c.deck_id = d.deck_id
GROUP BY d.deck_id, d.name, d.is_filtered;
GO
EXEC [{schema}].episteme_describe_view N'anki_deck_summary', N'Per deck: its cards by state and maturity, how many are suspended, buried, due, overdue or leeches, and the average interval, FSRS stability and difficulty. A deck counts only its own cards, not those of its subdecks.';
GO

CREATE OR ALTER VIEW [{schema}].anki_deck_settings AS
SELECT
    d.deck_id,
    d.name                                                              AS deck_name,
    o.options_id,
    o.name                                                              AS options_name,
    COALESCE(d.new_limit, o.new_per_day)                                AS new_per_day,
    COALESCE(d.review_limit, o.reviews_per_day)                         AS reviews_per_day,
    CAST(CASE WHEN d.new_limit IS NOT NULL OR d.review_limit IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS has_own_limits,
    o.learn_steps_minutes, o.relearn_steps_minutes,
    o.graduating_interval_good_days, o.graduating_interval_easy_days, o.maximum_interval_days,
    o.leech_threshold, o.leech_action,
    o.review_order, o.new_card_gather_priority, o.new_card_sort_order,
    c.fsrs_enabled,
    COALESCE(d.desired_retention, o.desired_retention)                  AS desired_retention,
    CAST(CASE WHEN EXISTS (SELECT 1 FROM OPENJSON(o.fsrs_params)) THEN 1 ELSE 0 END AS BIT) AS fsrs_optimized
FROM [{schema}].anki_decks AS d
JOIN [{schema}].anki_deck_options AS o ON o.options_id = d.options_id
CROSS JOIN [{schema}].anki_collection AS c
WHERE d.is_filtered = 0;
GO
EXEC [{schema}].episteme_describe_view N'anki_deck_settings', N'Per deck, the scheduling options that apply: its options preset, daily limits (the deck''s own if it overrides the preset''s), steps, intervals, leech rules, order, and FSRS desired retention.';
GO

CREATE OR ALTER VIEW [{schema}].anki_daily_reviews AS
SELECT
    review_day                                                          AS [day],
    COUNT(*)                                                            AS answers,
    COUNT(CASE WHEN kind = N'learning' THEN 1 END)                      AS learning,
    COUNT(CASE WHEN kind = N'review' THEN 1 END)                        AS reviewing,
    COUNT(CASE WHEN kind = N'relearning' THEN 1 END)                    AS relearning,
    COUNT(CASE WHEN rating = 1 THEN 1 END)                              AS again,
    COUNT(CASE WHEN rating = 2 THEN 1 END)                              AS hard,
    COUNT(CASE WHEN rating = 3 THEN 1 END)                              AS good,
    COUNT(CASE WHEN rating = 4 THEN 1 END)                              AS easy,
    ROUND(SUM(duration_seconds) / 60, 1)                                AS minutes,
    ROUND(1 - CAST(COUNT(CASE WHEN kind = N'review' AND rating = 1 THEN 1 END) AS FLOAT)
              / NULLIF(COUNT(CASE WHEN kind = N'review' THEN 1 END), 0), 4) AS retention
FROM [{schema}].anki_reviews
WHERE kind IN (N'learning', N'review', N'relearning', N'filtered')
GROUP BY review_day;
GO
EXEC [{schema}].episteme_describe_view N'anki_daily_reviews', N'Per Anki day: the answers given (by kind and by rating), the minutes spent, and the retention (the share of the answers to reviews, as opposed to learning steps, that were not Again). Manual changes are not counted.';
GO

CREATE OR ALTER VIEW [{schema}].anki_streaks AS
WITH days AS (
    SELECT DISTINCT review_day AS [day]
    FROM [{schema}].anki_reviews
    WHERE kind IN (N'learning', N'review', N'relearning', N'filtered')
),
numbered AS (
    SELECT [day], DATEADD(day, -CAST(ROW_NUMBER() OVER (ORDER BY [day]) AS INT), [day]) AS streak
    FROM days
)
SELECT
    MIN([day])                                                          AS start_day,
    MAX([day])                                                          AS end_day,
    COUNT(*)                                                            AS days,
    CAST(CASE WHEN MAX([day]) >= DATEADD(day, -1, (SELECT today FROM [{schema}].anki_today)) THEN 1 ELSE 0 END AS BIT) AS is_current
FROM numbered
GROUP BY streak;
GO
EXEC [{schema}].episteme_describe_view N'anki_streaks', N'Runs of consecutive Anki days with at least one answer; is_current is true for the run that includes today or yesterday.';
GO

CREATE OR ALTER VIEW [{schema}].anki_deck_retention AS
SELECT
    d.name                                                              AS deck_name,
    COUNT(*)                                                            AS reviews,
    ROUND(1 - AVG(CASE WHEN r.rating = 1 THEN CAST(1 AS FLOAT) ELSE CAST(0 AS FLOAT) END), 4) AS retention,
    COUNT(CASE WHEN r.review_day > DATEADD(day, -30, t.today) THEN 1 END) AS reviews_30d,
    ROUND(1 - AVG(CASE WHEN r.review_day > DATEADD(day, -30, t.today)
                       THEN CASE WHEN r.rating = 1 THEN CAST(1 AS FLOAT) ELSE CAST(0 AS FLOAT) END END), 4) AS retention_30d
FROM [{schema}].anki_reviews AS r
JOIN [{schema}].anki_cards AS c ON c.card_id = r.card_id
JOIN [{schema}].anki_decks AS d ON d.deck_id = c.deck_id
CROSS JOIN [{schema}].anki_today AS t
WHERE r.kind = N'review'
GROUP BY d.name;
GO
EXEC [{schema}].episteme_describe_view N'anki_deck_retention', N'Per deck: how many reviews (not learning steps) were answered, and the share that were not Again, over all time and over the last 30 days. Reviews of deleted cards are not counted.';
GO

CREATE OR ALTER VIEW [{schema}].anki_leeches AS
SELECT card_id, note_id, deck_name, tags, state, lapses, interval_days, is_suspended
FROM [{schema}].anki_card_details
WHERE is_leech = 1;
GO
EXEC [{schema}].episteme_describe_view N'anki_leeches', N'Cards tagged leech; sort by lapses to see the worst first.';
GO

CREATE OR ALTER VIEW [{schema}].anki_tag_summary AS
SELECT
    t.tag,
    COUNT(DISTINCT t.note_id)                                           AS notes,
    COUNT(c.card_id)                                                    AS cards,
    COUNT(CASE WHEN c.maturity = N'new' THEN 1 END)                     AS new,
    COUNT(CASE WHEN c.maturity = N'mature' THEN 1 END)                  AS mature,
    COUNT(CASE WHEN c.is_due = 1 THEN 1 END)                            AS due,
    COUNT(CASE WHEN c.is_leech = 1 THEN 1 END)                          AS leeches,
    CAST(COALESCE(SUM(CAST(c.lapses AS BIGINT)), 0) AS BIGINT)         AS lapses,
    ROUND(AVG(c.stability_days), 1)                                     AS avg_stability_days
FROM [{schema}].anki_note_tags AS t
LEFT JOIN [{schema}].anki_card_details AS c ON c.note_id = t.note_id
GROUP BY t.tag;
GO
EXEC [{schema}].episteme_describe_view N'anki_tag_summary', N'Per tag: its notes and cards, how many cards are new, mature, due or leeches, the lapses, and the average FSRS stability.';
