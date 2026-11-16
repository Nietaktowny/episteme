SELECT view_name AS name, comment FROM duckdb_views() WHERE NOT internal AND schema_name = 'main'
