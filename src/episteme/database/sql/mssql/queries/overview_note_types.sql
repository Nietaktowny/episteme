SELECT COALESCE(type, N'(none)') AS type, COUNT(*) AS notes
FROM [{schema}].vault_notes
GROUP BY COALESCE(type, N'(none)')
ORDER BY 2 DESC, 1
