-- Soft delete: only deleted_at is set; is_deleted follows. $event_ids is a list.
UPDATE events SET deleted_at = now() AT TIME ZONE 'UTC'
WHERE deleted_at IS NULL AND list_contains($event_ids, event_id)
RETURNING event_id
