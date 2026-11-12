#!/bin/sh
# Scheduled backups: a PostgreSQL dump and a mirror of the object store, every BACKUP_INTERVAL
# seconds, keeping the newest BACKUP_KEEP dumps. Runs in the "backup" service.
set -eu
: "${BACKUP_INTERVAL:=3600}"
: "${BACKUP_KEEP:=48}"
mkdir -p /backups/postgres /backups/objects

run_once() {
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  pg_dump --format=custom --file="/backups/postgres/opsinbox-$stamp.dump.partial" \
    "postgresql://opsinbox:opsinbox@postgres:5432/opsinbox"
  mv "/backups/postgres/opsinbox-$stamp.dump.partial" "/backups/postgres/opsinbox-$stamp.dump"
  mc alias set store http://minio:9000 opsinbox opsinbox-secret >/dev/null
  mc mirror --quiet --overwrite store/opsinbox /backups/objects
  ls -1t /backups/postgres/*.dump | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
  echo "backup $stamp done"
}

if [ "${1:-}" = "once" ]; then
  run_once
  exit 0
fi
while true; do
  run_once || echo "backup failed, will retry"
  sleep "$BACKUP_INTERVAL"
done
