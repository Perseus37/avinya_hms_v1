#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/docker-compose.prod.yml}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
GPG_PASSPHRASE_FILE="${GPG_PASSPHRASE_FILE:?Set GPG_PASSPHRASE_FILE to a protected file outside the repository.}"

if [[ ! -r "$ENV_FILE" || ! -r "$COMPOSE_FILE" ]]; then
  echo "Production env or Compose file is not readable." >&2
  exit 2
fi
if [[ ! -r "$GPG_PASSPHRASE_FILE" || ! -s "$GPG_PASSPHRASE_FILE" ||
      ! "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Passphrase file must be readable/non-empty and RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi
passphrase_bytes="$(wc -c < "$GPG_PASSPHRASE_FILE")"
if [[ ! "$passphrase_bytes" =~ ^[0-9]+$ ]] || (( passphrase_bytes < 32 )); then
  echo "GPG passphrase file must contain at least 32 random ASCII characters." >&2
  exit 2
fi
for tool in docker gpg sha256sum flock; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Required command is unavailable: $tool" >&2
    exit 2
  }
done
command -v findmnt >/dev/null 2>&1 || {
  echo "Required command is unavailable: findmnt" >&2
  exit 2
}

read_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

BACKUP_ROOT="${BACKUP_ROOT:-$(read_env_value BACKUP_HOST_PATH)}"
BACKUP_MOUNTPOINT="${BACKUP_MOUNTPOINT:-$(read_env_value BACKUP_STORAGE_MOUNTPOINT)}"
ATTACHMENTS_PATH="${ATTACHMENTS_PATH:-$(read_env_value ATTACHMENTS_HOST_PATH)}"
CONTROL_PLANE_DB_NAME="$(read_env_value CONTROL_PLANE_DB_NAME)"
TENANT_DATABASE_PREFIX="$(read_env_value TENANT_DATABASE_PREFIX)"

if [[ "$BACKUP_ROOT" != /* || "$BACKUP_ROOT" == "/" ||
      "$BACKUP_MOUNTPOINT" != /* || "$BACKUP_MOUNTPOINT" == "/" ]]; then
  echo "Backup root and storage mountpoint must be absolute, non-root host paths." >&2
  exit 2
fi
if [[ ! "$CONTROL_PLANE_DB_NAME" =~ ^[a-z][a-z0-9_]{0,62}$ ||
      ! "$TENANT_DATABASE_PREFIX" =~ ^[a-z][a-z0-9_]{0,12}$ ]]; then
  echo "Database name or tenant prefix in the production env is invalid." >&2
  exit 2
fi
if [[ -n "$ATTACHMENTS_PATH" && "$ATTACHMENTS_PATH" != /* ]]; then
  echo "ATTACHMENTS_HOST_PATH must be an absolute path when configured." >&2
  exit 2
fi

mkdir -p -- "$BACKUP_ROOT"
chmod 700 -- "$BACKUP_ROOT"
backup_mount_target="$(findmnt --noheadings --output TARGET --target "$BACKUP_ROOT" | xargs)"
expected_mountpoint="$(realpath -- "$BACKUP_MOUNTPOINT")"
if [[ "$backup_mount_target" != "$expected_mountpoint" ]]; then
  echo "Backup storage is not mounted at BACKUP_STORAGE_MOUNTPOINT; refusing to write to the root disk." >&2
  exit 2
fi
exec 9>"$BACKUP_ROOT/.backup.lock"
flock -n 9 || {
  echo "Another production database backup is already running." >&2
  exit 1
}

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
sql="SELECT datname FROM pg_database WHERE datistemplate = false AND (datname = '$CONTROL_PLANE_DB_NAME' OR left(datname, ${#TENANT_DATABASE_PREFIX}) = '$TENANT_DATABASE_PREFIX') ORDER BY datname;"
database_output="$("${compose[@]}" exec -T db sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -At -v ON_ERROR_STOP=1 -c "$1"' sh "$sql")"
mapfile -t databases <<<"$database_output"
if [[ "${#databases[@]}" -eq 0 ]] || ! printf '%s\n' "${databases[@]}" | grep -Fxq -- "$CONTROL_PLANE_DB_NAME"; then
  echo "No application databases found, or the control-plane DB is missing." >&2
  exit 1
fi
for database in "${databases[@]}"; do
  if [[ ! "$database" =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
    echo "Database name returned by PostgreSQL is not safe for a filename." >&2
    exit 1
  fi
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_name="avinya-pg17-${stamp}-$$"
staging_dir="$(mktemp -d "$BACKUP_ROOT/.${backup_name}.partial.XXXXXX")"
final_dir="$BACKUP_ROOT/$backup_name"
cleanup() {
  if [[ -n "${staging_dir:-}" && -d "$staging_dir" ]]; then
    rm -rf -- "$staging_dir"
  fi
}
trap cleanup EXIT

for database in "${databases[@]}"; do
  echo "Dumping database: $database"
  "${compose[@]}" exec -T db sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$1" --format=custom --no-owner --no-acl' sh "$database" \
    | gpg --batch --quiet --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-file "$GPG_PASSPHRASE_FILE" \
        --output "$staging_dir/$database.dump.gpg"
  gpg --batch --quiet --pinentry-mode loopback --decrypt --passphrase-file "$GPG_PASSPHRASE_FILE" \
    "$staging_dir/$database.dump.gpg" \
    | "${compose[@]}" exec -T db pg_restore --list
done

"${compose[@]}" exec -T db sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" --globals-only --no-role-passwords' \
  | gpg --batch --quiet --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 \
      --passphrase-file "$GPG_PASSPHRASE_FILE" \
      --output "$staging_dir/postgres-globals.sql.gpg"
gpg --batch --quiet --pinentry-mode loopback --decrypt \
  --passphrase-file "$GPG_PASSPHRASE_FILE" \
  "$staging_dir/postgres-globals.sql.gpg" >/dev/null

if [[ "${INCLUDE_ATTACHMENTS:-false}" == "true" ]]; then
  if [[ -z "$ATTACHMENTS_PATH" || ! -d "$ATTACHMENTS_PATH" ]]; then
    echo "Attachment backup requested, but ATTACHMENTS_HOST_PATH is unavailable." >&2
    exit 2
  fi
  backup_real="$(realpath -- "$BACKUP_ROOT")"
  attachments_real="$(realpath -- "$ATTACHMENTS_PATH")"
  if [[ "$attachments_real" == "$backup_real" || "$attachments_real" == "$backup_real/"* ||
        "$backup_real" == "$attachments_real/"* ]]; then
    echo "Backup and attachment paths must be distinct directories, not nested." >&2
    exit 2
  fi
  echo "Archiving attachment volume: $ATTACHMENTS_PATH"
  tar --create --gzip --file=- --directory="$ATTACHMENTS_PATH" . \
    | gpg --batch --quiet --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-file "$GPG_PASSPHRASE_FILE" \
        --output "$staging_dir/attachments.tar.gz.gpg"
  gpg --batch --quiet --pinentry-mode loopback --decrypt \
    --passphrase-file "$GPG_PASSPHRASE_FILE" \
    "$staging_dir/attachments.tar.gz.gpg" | tar --list --gzip --file=- >/dev/null
fi

(
  cd -- "$staging_dir"
  sha256sum -- *.gpg > SHA256SUMS
  sha256sum --check SHA256SUMS
)
mv -- "$staging_dir" "$final_dir"
staging_dir=""
echo "Encrypted PostgreSQL backup completed: $final_dir"

if [[ -n "${BACKUP_REMOTE_DEST:-}" ]]; then
  command -v rsync >/dev/null 2>&1 || {
    echo "BACKUP_REMOTE_DEST is set but rsync is unavailable." >&2
    exit 2
  }
  rsync -a -- "$final_dir/" "${BACKUP_REMOTE_DEST%/}/$backup_name/"
  echo "Encrypted backup copied to remote destination."
fi

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
  -name 'avinya-pg17-*' -mtime "+$RETENTION_DAYS" -exec rm -rf -- {} +
