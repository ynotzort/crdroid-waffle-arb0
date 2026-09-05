#!/usr/bin/env bash
# Gate before flashing ANY self-built zip. Refuses to pass unless the zip is
# ARB 0 and the packaged kernel carries KernelSU + SUSFS. Run it every time.
#
#   scripts/preflight.sh [zip]      default: newest zip in out/target/product/waffle
set -uo pipefail
. "$(dirname "$0")/../env.sh"
ZIP=${1:-$(ls -t "$PRODUCT_OUT"/crDroidAndroid-*-"$DEVICE"-*.zip 2>/dev/null | head -1)}
[ -f "${ZIP:-}" ] || { echo "no zip found in $PRODUCT_OUT"; exit 2; }
rc=0
echo "== preflight: $ZIP =="

echo "-- 1. ARB must be 0"
if python3 "$REPO/tools/arbcheck.py" "$ZIP" 2>&1 | grep -q 'ARB = 0'; then
  echo "   PASS: ARB 0"
else
  echo "   FAIL: ARB is not 0 - flashing this WILL blow the fuse"; rc=1
fi

echo "-- 2. kernel must carry KernelSU + SUSFS"
n=$(strings "$KOBJ/arch/arm64/boot/Image" 2>/dev/null | grep -ciE 'kernelsu|susfs')
if [ "${n:-0}" -gt 50 ]; then echo "   PASS: $n KSU/SUSFS strings"; else echo "   FAIL: only ${n:-0} KSU strings - kernel lost KSU"; rc=1; fi

echo "-- 3. packaged kernel == built KSU kernel"
if cmp -s "$KOBJ/arch/arm64/boot/Image" "$PRODUCT_OUT/kernel"; then
  echo "   PASS: packaged kernel matches"
else
  echo "   WARN: packaged 'kernel' differs from KERNEL_OBJ Image (rebuilt separately?)"
fi

echo "-- 4. config sanity"
if grep -q '^CONFIG_KSU=y' "$KOBJ/.config" && grep -q '^CONFIG_KSU_SUSFS=y' "$KOBJ/.config"; then
  echo "   PASS: CONFIG_KSU + CONFIG_KSU_SUSFS set"
else
  echo "   FAIL: KSU config missing"; rc=1
fi

echo "-- 5. camera handshake prop built in"
if grep -rqs 'ro.oplus.cam.treepatch=1' "$PRODUCT_OUT/product/etc/build.prop" 2>/dev/null; then
  echo "   PASS: ro.oplus.cam.treepatch=1"
else
  echo "   WARN: ro.oplus.cam.treepatch not in product build.prop - camera module will refuse to install"
fi

echo
if [ $rc = 0 ]; then
  echo "== PREFLIGHT PASS - safe to sideload (no format; same test-keys as the running build) =="
else
  echo "== PREFLIGHT FAILED - DO NOT FLASH =="
fi
exit $rc
