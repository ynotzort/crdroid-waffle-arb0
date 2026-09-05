#!/usr/bin/env bash
# What state is the tree in? Read-only.
set -uo pipefail
. "$(dirname "$0")/../env.sh"
cd "$AOSP_TOP"

echo "== ARB0 swap ($VENDOR) =="
if [ -f "$VENDOR/radio/xbl_config.img" ]; then
  python3 "$REPO/tools/arbcheck.py" "$VENDOR/radio/xbl_config.img" 2>&1 | grep "ARB ="
  ( cd "$VENDOR/radio" && sha1sum -c --quiet "$REPO/arb0/manifest.sha1" 2>/dev/null ) \
    && echo "  all 4 blobs match arb0/manifest.sha1" || echo "  blobs are NOT the ARB0 donor set (upstream state?)"
else
  echo "  vendor tree missing"
fi

echo "== KSU + SUSFS ($KERNEL) =="
if [ -d "$KERNEL/KernelSU-Next" ]; then
  echo "  KernelSU-Next: $(git -C "$KERNEL/KernelSU-Next" log --oneline -1)"
  [ "$(git -C "$KERNEL/KernelSU-Next" rev-parse HEAD)" = "$KSU_NEXT_COMMIT" ] && echo "  (pinned commit)" || echo "  (NOT the pinned commit $KSU_NEXT_COMMIT)"
else
  echo "  KernelSU-Next: absent"
fi
[ -L "$KERNEL/drivers/kernelsu" ] && echo "  drivers/kernelsu symlink: yes" || echo "  drivers/kernelsu symlink: NO"
if [ -f "$WORK/susfs4ksu/kernel_patches/$SUSFS_PATCH" ]; then
  ( cd "$KERNEL" && patch -p1 -R --dry-run -f -i "$WORK/susfs4ksu/kernel_patches/$SUSFS_PATCH" >/dev/null 2>&1 ) \
    && echo "  susfs patch: applied" || echo "  susfs patch: NOT applied (or tree moved)"
else
  echo "  susfs patch: unknown - work/susfs4ksu not cloned yet"
fi
grep -q '^CONFIG_KSU_SUSFS=y' "$KERNEL/arch/arm64/configs/vendor/ksu.config" 2>/dev/null && echo "  ksu.config: present" || echo "  ksu.config: MISSING"
grep -q 'vendor/ksu.config' "$DEVCOMMON/BoardConfigCommon.mk" && echo "  BoardConfigCommon.mk: wired" || echo "  BoardConfigCommon.mk: NOT wired"

echo "== camera tree edits =="
if [ -x "$OPLUSCAM_REPO/tools/treepatch.py" ]; then
  ANDROID_BUILD_TOP=$AOSP_TOP OPLUSCAM_DEVICE=$DEVICE "$OPLUSCAM_REPO/tools/treepatch.py" status
else
  echo "  oplus-camera-waffle not found at $OPLUSCAM_REPO"
fi

"$REPO/scripts/denag.sh" --status

echo "== working-tree changes in the touched projects =="
for p in vendor/oneplus/$DEVICE kernel/oneplus/sm8650 device/oneplus/sm8650-common device/oneplus/$DEVICE \
         frameworks/native frameworks/av frameworks/base hardware/oplus packages/apps/Settings; do
  n=$(git -C "$p" status --short 2>/dev/null | wc -l)
  printf '  %-34s %s\n' "$p" "$n changed/untracked"
done
