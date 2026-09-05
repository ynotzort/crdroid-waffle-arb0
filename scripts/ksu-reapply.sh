#!/usr/bin/env bash
# Re-integrate KernelSU-Next (dev-susfs) + SUSFS into the kernel tree. Idempotent.
#
#   scripts/ksu-reapply.sh            pinned commits from versions.env
#   scripts/ksu-reapply.sh --latest   fetch branch heads instead, print the new
#                                     commits so versions.env can be updated
#
# What it does (ROM.md section 13):
#   1. KernelSU-Next checkout at kernel/oneplus/sm8650/KernelSU-Next, symlinked
#      as drivers/kernelsu, wired into drivers/Makefile and drivers/Kconfig.
#      Same wiring KernelSU's setup.sh does, without its git stash/pull.
#   2. SUSFS: fs/susfs.c + include/linux/susfs*.h copied in, the 50_ kernel
#      patch applied (23 files; applied clean and fuzz-free to OnePlus's tree).
#      That patch also carries KSU's manual hooks - no separate hooks patch.
#   3. arch/arm64/configs/vendor/ksu.config + its TARGET_KERNEL_CONFIG entry.
# The susfs4ksu clone lives in work/, never inside the Android tree.
set -euo pipefail
. "$(dirname "$0")/../env.sh"
LATEST=${1:-}

cd "$KERNEL"
echo "== KernelSU-Next ($KSU_NEXT_BRANCH) =="
[ -d KernelSU-Next ] || git clone --no-checkout "$KSU_NEXT_REPO" KernelSU-Next
if [ "$LATEST" = "--latest" ]; then
  git -C KernelSU-Next fetch origin "$KSU_NEXT_BRANCH"
  git -C KernelSU-Next checkout -q -B "$KSU_NEXT_BRANCH" FETCH_HEAD
else
  git -C KernelSU-Next cat-file -e "$KSU_NEXT_COMMIT^{commit}" 2>/dev/null \
    || git -C KernelSU-Next fetch origin "$KSU_NEXT_BRANCH"
  git -C KernelSU-Next checkout -q "$KSU_NEXT_COMMIT"
fi
echo "  at $(git -C KernelSU-Next log --oneline -1)"
ln -sfn ../KernelSU-Next/kernel drivers/kernelsu
grep -q 'kernelsu/' drivers/Makefile || printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >> drivers/Makefile
grep -q 'drivers/kernelsu/Kconfig' drivers/Kconfig || sed -i '/^endmenu/i source "drivers/kernelsu/Kconfig"' drivers/Kconfig

echo "== susfs4ksu ($SUSFS_BRANCH) =="
S=$WORK/susfs4ksu
[ -d "$S" ] || git clone --no-checkout -b "$SUSFS_BRANCH" "$SUSFS_REPO" "$S"
if [ "$LATEST" = "--latest" ]; then
  git -C "$S" fetch origin "$SUSFS_BRANCH"
  git -C "$S" checkout -q -B "$SUSFS_BRANCH" FETCH_HEAD
else
  git -C "$S" cat-file -e "$SUSFS_COMMIT^{commit}" 2>/dev/null || git -C "$S" fetch origin "$SUSFS_BRANCH"
  git -C "$S" checkout -q "$SUSFS_COMMIT"
fi
echo "  at $(git -C "$S" log --oneline -1)"
cp -p "$S/kernel_patches/fs/susfs.c" fs/
cp -p "$S/kernel_patches/include/linux/susfs.h" "$S/kernel_patches/include/linux/susfs_def.h" include/linux/
P=$S/kernel_patches/$SUSFS_PATCH
if patch -p1 -R --dry-run -f -i "$P" >/dev/null 2>&1; then
  echo "  $SUSFS_PATCH already applied (reverse dry-run clean)"
elif patch -p1 --dry-run -f -i "$P" >/dev/null 2>&1; then
  patch -p1 -f -i "$P"
else
  echo "!! $SUSFS_PATCH neither applies nor reverse-applies cleanly - the kernel tree"
  echo "   has moved under it, or a different SUSFS version is half-applied. Inspect:"
  echo "   patch -p1 --dry-run -i $P"
  exit 1
fi

echo "== config =="
cat > arch/arm64/configs/vendor/ksu.config <<'CFG'
# KernelSU-Next (pershoot dev-susfs) + SUSFS
# Sub-options default to y in drivers/kernelsu/Kconfig; only the top-level
# switches are pinned here. dev-susfs has no hook-mode option - manual hooks
# are unconditional, supplied by the susfs 50_ kernel patch.
CONFIG_KSU=y
CONFIG_KSU_SUSFS=y
CFG
if grep -q 'vendor/ksu.config' "$DEVCOMMON/BoardConfigCommon.mk"; then
  echo "  TARGET_KERNEL_CONFIG already lists vendor/ksu.config"
else
  sed -i 's#vendor/oplus/pineapple_GKI.config#vendor/oplus/pineapple_GKI.config \\\n    vendor/ksu.config#' "$DEVCOMMON/BoardConfigCommon.mk"
  grep -q 'vendor/ksu.config' "$DEVCOMMON/BoardConfigCommon.mk" || { echo "!! anchor not found in BoardConfigCommon.mk"; exit 1; }
  echo "  added vendor/ksu.config to TARGET_KERNEL_CONFIG"
fi

if [ "$LATEST" = "--latest" ]; then
  echo
  echo "Moved to branch heads. If the build + preflight pass, pin them:"
  echo "  KSU_NEXT_COMMIT=$(git -C KernelSU-Next rev-parse HEAD)"
  echo "  SUSFS_COMMIT=$(git -C "$S" rev-parse HEAD)"
fi
echo "KSU+SUSFS in place. Build, then scripts/preflight.sh before flashing."
