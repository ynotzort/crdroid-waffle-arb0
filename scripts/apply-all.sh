#!/usr/bin/env bash
# The three integrations, in one go. Run after every `repo sync`. Idempotent.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
"$HERE/ksu-reapply.sh"
echo
"$HERE/arb0-reapply.sh"
echo
"$HERE/camera-apply.sh" "$@"
echo
"$HERE/status.sh"
