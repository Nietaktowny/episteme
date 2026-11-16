-- $paths is a list of paths, sent as JSON.
DELETE FROM [{schema}].notes OUTPUT deleted.path
WHERE path IN (SELECT j.value COLLATE Latin1_General_100_BIN2 FROM OPENJSON($paths) AS j)
