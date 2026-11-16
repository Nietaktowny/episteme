-- Only the collection settings Episteme understands; the values are JSON.
SELECT key, val FROM config WHERE key IN ('rollover', 'creationOffset', 'fsrs', 'schedVer')
