DELETE FROM tasks WHERE list_contains($task_ids, task_id) RETURNING task_id
