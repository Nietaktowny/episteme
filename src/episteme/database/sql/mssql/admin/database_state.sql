-- Run on master: how far along is a database that was just created? ONLINE when it can be used.
SELECT state_desc FROM sys.databases WHERE name = $name
