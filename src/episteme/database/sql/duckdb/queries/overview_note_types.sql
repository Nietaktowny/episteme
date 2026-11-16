SELECT COALESCE(type, '(none)') AS type, count(*) AS notes
FROM vault_notes
GROUP BY 1
ORDER BY 2 DESC, 1
