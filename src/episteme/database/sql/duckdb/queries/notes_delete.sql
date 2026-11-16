DELETE FROM notes WHERE list_contains($paths, path) RETURNING path
