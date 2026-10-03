#!/usr/bin/env bash
# One non-overlapping Inbox ingest followed by guarded Library imports and mirroring.
set -euo pipefail
umask 077

env_file=${TEXTSTRATA_BRIDGE_ENV_FILE:-"$HOME/.config/textstrata/google-bridge.env"}
if [[ ! -f "$env_file" ]]; then
  printf 'Missing private bridge environment file: %s\n' "$env_file" >&2
  exit 1
fi

set -a
# The file is private shell configuration owned by the operator.
# shellcheck source=/dev/null
source "$env_file"
set +a

: "${TEXTSTRATA_SOURCES_CONFIG:?Set TEXTSTRATA_SOURCES_CONFIG in the private environment file}"
: "${TEXTSTRATA_GOOGLE_BRIDGE_URL:?Set TEXTSTRATA_GOOGLE_BRIDGE_URL in the private environment file}"
: "${TEXTSTRATA_GOOGLE_BRIDGE_SECRET:?Set TEXTSTRATA_GOOGLE_BRIDGE_SECRET in the private environment file}"
: "${TEXTSTRATA_WORKSPACE:?Set TEXTSTRATA_WORKSPACE in the private environment file}"
: "${TEXTSTRATA_STATE_DIR:?Set TEXTSTRATA_STATE_DIR in the private environment file}"
: "${TEXTSTRATA_BIN:?Set TEXTSTRATA_BIN to the absolute CLI path in the private environment file}"

mkdir -p "$TEXTSTRATA_STATE_DIR"
exec 9>"$TEXTSTRATA_STATE_DIR/google-bridge-sync.lock"
flock -n 9 || exit 0

"$TEXTSTRATA_BIN" ingest google-bridge
"$TEXTSTRATA_BIN" mirror google-bridge
