#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

# Run from the repo root so relative paths in docker-compose work as expected.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

compose=(docker compose -f "$COMPOSE_FILE")

echo "==> Compose image(s):"
images="$("${compose[@]}" config --images)"
printf '%s\n' "$images"

remove_image_if_present() {
  local image_ref="$1"

  # docker accepts either repo or repo:tag; if repo only, it will default to :latest.
  if docker image inspect "$image_ref" >/dev/null 2>&1; then
    docker rmi -f "$image_ref" >/dev/null 2>&1 || true
  fi
}

remove_repo_tags_if_present() {
  local repo="$1"

  # Remove all tags for a given repository name (without relying on rg/grep).
  docker images --format '{{.Repository}}:{{.Tag}}' | while read -r ref; do
    case "$ref" in
      "${repo}":*)
        docker rmi -f "$ref" >/dev/null 2>&1 || true
        ;;
    esac
  done
}

echo "==> Removing previous image(s) (if any)..."
while IFS= read -r img; do
  # Skip empty lines.
  [[ -z "${img}" ]] && continue

  if [[ "$img" == *:* ]]; then
    # If Compose ever returns a repo:tag, remove it exactly (plus let docker handle :latest defaults).
    remove_image_if_present "$img" || true
  else
    # Compose output for build-only services is typically repo name only.
    remove_image_if_present "${img}" || true
    remove_image_if_present "${img}:latest" || true
    remove_repo_tags_if_present "$img" || true
  fi
done <<< "$images"

echo "==> Building image (no cache)..."
"${compose[@]}" build --no-cache

echo "==> Starting containers..."
# Force recreation so the freshly built image is definitely used.
"${compose[@]}" up -d --remove-orphans --force-recreate

echo "==> Deploy complete."

