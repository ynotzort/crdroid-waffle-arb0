# Sourced by every script. Resolves the Android tree and this repo's paths.
#
#   AOSP_TOP   the Android build tree. Taken from $ANDROID_BUILD_TOP (set by
#              `source build/envsetup.sh`), then $AOSP_TOP, then /aosp.
#   REPO       this checkout
#   WORK       $REPO/work - clones, blob stash, logs. Git-ignored. Lives OUTSIDE
#              the Android tree on purpose: anything with an Android.mk under
#              the build root becomes part of the build (ROM.md section 13).
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=$REPO/work
# shellcheck source=versions.env
. "$REPO/versions.env"

if [ -n "${ANDROID_BUILD_TOP:-}" ] && [ -d "$ANDROID_BUILD_TOP/build" ]; then
  AOSP_TOP=$ANDROID_BUILD_TOP
elif [ -n "${AOSP_TOP:-}" ] && [ -d "$AOSP_TOP/build" ]; then
  :
elif [ -d /aosp/build ]; then
  AOSP_TOP=/aosp
else
  echo "cannot find the Android tree - set ANDROID_BUILD_TOP or AOSP_TOP" >&2
  return 1 2>/dev/null || exit 1
fi
export AOSP_TOP REPO WORK DEVICE

KERNEL=$AOSP_TOP/kernel/oneplus/sm8650
DEVCOMMON=$AOSP_TOP/device/oneplus/sm8650-common
VENDOR=$AOSP_TOP/vendor/oneplus/$DEVICE
PRODUCT_OUT=$AOSP_TOP/out/target/product/$DEVICE
KOBJ=$PRODUCT_OUT/obj/KERNEL_OBJ
ARB0_STASH=$WORK/arb0-stash
mkdir -p "$WORK/logs"

# The camera port lives in its own repo. Sibling checkout by default.
OPLUSCAM_REPO=${OPLUSCAM_REPO:-$(dirname "$REPO")/oplus-camera-waffle}
