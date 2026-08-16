#!/usr/bin/env bash
# R-F4036 — prevent repository edits from mutating an in-flight training driver.
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: run_immutable_shell.sh SCRIPT [ARG ...]" >&2; exit 64; }
SOURCE=$1
shift
[ -f "$SOURCE" ] || { echo "immutable source missing: $SOURCE" >&2; exit 66; }

SNAPSHOT=$(mktemp "${TMPDIR:-/tmp}/aria-training-driver.XXXXXX.sh")
cp -- "$SOURCE" "$SNAPSHOT"
chmod 700 "$SNAPSHOT"
export ARIA_DRIVER_SOURCE="$SOURCE"
exec bash "$SNAPSHOT" "$@"
