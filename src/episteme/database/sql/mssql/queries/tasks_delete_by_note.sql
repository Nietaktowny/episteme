DELETE FROM [{schema}].tasks OUTPUT deleted.task_id
WHERE note_path IN (SELECT j.value COLLATE Latin1_General_100_BIN2 FROM OPENJSON($paths) AS j)
