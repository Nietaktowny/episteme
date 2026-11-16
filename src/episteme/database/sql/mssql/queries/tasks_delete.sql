DELETE FROM [{schema}].tasks OUTPUT deleted.task_id
WHERE task_id IN (SELECT j.value COLLATE Latin1_General_100_BIN2 FROM OPENJSON($task_ids) AS j)
