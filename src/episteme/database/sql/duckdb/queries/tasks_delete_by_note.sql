DELETE FROM tasks WHERE list_contains($paths, note_path) RETURNING task_id
