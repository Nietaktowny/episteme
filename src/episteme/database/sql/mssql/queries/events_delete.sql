-- Soft delete: only deleted_at is set; is_deleted follows. $event_ids is a list, sent as JSON.
UPDATE [{schema}].events SET deleted_at = SYSUTCDATETIME()
OUTPUT inserted.event_id
WHERE deleted_at IS NULL
  AND event_id IN (SELECT j.value COLLATE Latin1_General_100_BIN2 FROM OPENJSON($event_ids) AS j)
