-- Create the schema of Episteme if the database does not have it. Run once when the engine connects, unless the schema is dbo.
-- CREATE SCHEMA must be alone in its batch, hence EXEC.
IF SCHEMA_ID(N'{schema}') IS NULL EXEC (N'CREATE SCHEMA [{schema}]')
