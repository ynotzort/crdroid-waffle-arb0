#!/usr/bin/env bash
# Build the ROM with ccache, log to work/logs/, then run preflight.
#
#   scripts/build.sh            brunch waffle (full ROM)
#   scripts/build.sh bootimage  m bootimage (kernel-only iteration)
#
# Memory: soong_build peaks near 28 GiB RSS on this tree. On a 32 GiB host that
# needs real disk swap (ROM.md section 7); zram alone does not cover it.
set -uo pipefail
. "$(dirname "$0")/../env.sh"
TARGET=${1:-rom}
LOG=$WORK/logs/build-$(date +%Y%m%d-%H%M%S)-$TARGET.log
cd "$AOSP_TOP"
export USE_CCACHE=1 CCACHE_EXEC=$(command -v ccache) CCACHE_DIR=${CCACHE_DIR:-$AOSP_TOP/.ccache}
ccache -M 50G >/dev/null 2>&1 || true
{
  echo "=== $TARGET build start $(date -Is) ==="
  # shellcheck disable=SC1091
  source build/envsetup.sh >/dev/null 2>&1
  if [ "$TARGET" = bootimage ]; then
    breakfast "$DEVICE" >/dev/null 2>&1 && m bootimage
  else
    brunch "$DEVICE"
  fi
  rc=$?
  echo "=== build end $(date -Is) rc=$rc ==="
  ls -lh "$PRODUCT_OUT"/*.zip 2>/dev/null
  exit $rc
} 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
echo "log: $LOG"
[ $rc = 0 ] && [ "$TARGET" = rom ] && "$REPO/scripts/preflight.sh"
exit $rc
