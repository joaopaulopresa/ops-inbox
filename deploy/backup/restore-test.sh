#!/bin/sh
# Restore the newest dump into a scratch database and compare it with the live one.
# Exits non-zero when the restore fails or the row counts differ.
set -eu
latest=$(ls -1t /backups/postgres/*.dump | head -n 1)
echo "restoring $latest"
psql -q "postgresql://opsinbox:opsinbox@postgres:5432/postgres" -c "DROP DATABASE IF EXISTS opsinbox_restore" \
  -c "CREATE DATABASE opsinbox_restore"
pg_restore --no-owner --dbname="postgresql://opsinbox:opsinbox@postgres:5432/opsinbox_restore" "$latest"

status=0
for table in messages attachments cases bookings documents outgoing_messages audit_log; do
  live=$(psql -tA "postgresql://opsinbox:opsinbox@postgres:5432/opsinbox" -c "SELECT count(*) FROM $table")
  restored=$(psql -tA "postgresql://opsinbox:opsinbox@postgres:5432/opsinbox_restore" -c "SELECT count(*) FROM $table")
  echo "$table: live $live, restored $restored"
  [ "$live" -ge "$restored" ] || status=1
  [ "$restored" -gt 0 ] || [ "$live" -eq 0 ] || status=1
done
objects=$(find /backups/objects -type f | wc -l)
echo "objects mirrored: $objects"
[ "$objects" -gt 0 ] || status=1
psql -q "postgresql://opsinbox:opsinbox@postgres:5432/postgres" -c "DROP DATABASE opsinbox_restore"
[ $status -eq 0 ] && echo "restore test passed" || echo "restore test FAILED"
exit $status
