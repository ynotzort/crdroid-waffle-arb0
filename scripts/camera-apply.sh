#!/usr/bin/env bash
# Apply the OPlus stock camera tree edits from the oplus-camera-waffle repo.
# Idempotent (treepatch.py skips what is already there, aborts on foreign edits).
#
# The camera itself - app + blobs - is a KernelSU module built by that repo and
# installed on the phone; these tree edits are what make it work on the ROM
# side (frameworks/native, frameworks/av, frameworks/base, hardware/oplus,
# device.mk). Set OPLUSCAM_REPO if the checkout is not a sibling directory.
set -euo pipefail
. "$(dirname "$0")/../env.sh"
TP=$OPLUSCAM_REPO/tools/treepatch.py
[ -x "$TP" ] || { echo "!! $TP not found - clone oplus-camera-waffle next to this repo or set OPLUSCAM_REPO"; exit 1; }
ANDROID_BUILD_TOP=$AOSP_TOP OPLUSCAM_DEVICE=$DEVICE "$TP" apply "$@"
