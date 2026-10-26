#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/docker-compose.prod.yml}"
GPG_PASSPHRASE_FILE="${GPG_PASSPHRASE_FILE:?Set GPG_PASSPHRASE_FILE to a protected file outside the repository.}"

usage() {
  echo "Usage: $0 --file ENCRYPTED_DUMP --target-db restore_name"
}

backup_file=""
target_db=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      backup_file="$2"
      shift 2
      ;;
    --target-db)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      target_db="$2"
      shift 2
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$backup_file" || ! -r "$GPG_PASSPHRASE_FILE" ||
      ! -s "$GPG_PASSPHRASE_FILE" ||
      ! -r "$ENV_FILE" || ! -r "$COMPOSE_FILE" ]]; then
  echo "Backup, passphrase, production env, or Compose file is unavailable." >&2
  exit 2
fi
passphrase_bytes="$(wc -c < "$GPG_PASSPHRASE_FILE")"
if [[ ! "$passphrase_bytes" =~ ^[0-9]+$ ]] || (( passphrase_bytes < 32 )); then
  echo "GPG passphrase file must contain at least 32 random ASCII characters." >&2
  exit 2
fi
if [[ ! "$target_db" =~ ^restore_[a-z][a-z0-9_]{0,54}$ ]]; then
  echo "Target DB must be a new isolated name beginning with restore_." >&2
  exit 2
fi

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
command -v sha256sum >/dev/null 2>&1 || {
  echo "Required command is unavailable: sha256sum" >&2
  exit 2
}
snapshot_dir="$(cd -- "$(dirname -- "$backup_file")" && pwd)"
backup_file="$snapshot_dir/$(basename -- "$backup_file")"
if [[ -f "$snapshot_dir/SHA256SUMS" ]]; then
  (cd -- "$snapshot_dir" && sha256sum --check SHA256SUMS)
fi
gpg --batch --quiet --pinentry-mode loopback --decrypt \
  --passphrase-file "$GPG_PASSPHRASE_FILE" "$backup_file" \
  | "${compose[@]}" exec -T db pg_restore --list

database_exists="$("${compose[@]}" exec -T db sh -ec "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h 127.0.0.1 -p \"\$POSTGRES_PORT\" -U \"\$POSTGRES_USER\" -d postgres -At -v ON_ERROR_STOP=1 -c \"SELECT 1 FROM pg_database WHERE datname = '$target_db';\"")"
if [[ "$database_exists" == "1" ]]; then
  echo "Refusing to overwrite existing database: $target_db" >&2
  exit 1
fi

"${compose[@]}" exec -T db sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" createdb -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$1"' sh "$target_db"
echo "Restoring into isolated database: $target_db"
if ! gpg --batch --quiet --pinentry-mode loopback --decrypt --passphrase-file "$GPG_PASSPHRASE_FILE" "$backup_file" \
  | "${compose[@]}" exec -T db pg_restore --exit-on-error --no-owner --no-acl --dbname "$target_db"; then
  echo "Restore failed; the isolated target was retained for diagnosis. Do not route tenants to it." >&2
  exit 1
fi
echo "Restore completed in $target_db. Verify revision, schema, records, and application behavior before any cutover."
