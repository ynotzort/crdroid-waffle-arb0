#!/usr/bin/env bash
# Dirty-flash a self-built update (ROM.md section 14). Same test-keys as the
# running build, so no format: data, modules, root hiding all survive.
#
# Runs preflight first and refuses to continue on failure. Each fastboot step
# is confirmed interactively - this script never flashes unattended.
set -euo pipefail
. "$(dirname "$0")/../env.sh"
ZIP=${1:-$(ls -t "$PRODUCT_OUT"/crDroidAndroid-*-"$DEVICE"-*.zip 2>/dev/null | head -1)}
"$REPO/scripts/preflight.sh" "$ZIP"

# Same-day rebuilds overwrite each other (the name embeds the date, not the
# time); copy what is about to be flashed so it is not lost.
KEEP=$WORK/flashed/$(date +%Y%m%d-%H%M%S)
mkdir -p "$KEEP"
cp -p "$ZIP" "$KEEP/"
for p in boot dtbo init_boot vbmeta vendor_boot recovery; do cp -p "$PRODUCT_OUT/$p.img" "$KEEP/"; done
( cd "$KEEP" && sha256sum ./* > SHA256SUMS )
echo "copied to $KEEP"

cat <<STEPS

Steps (the bootloader must already be unlocked; no unlock_critical is needed -
update_engine writes the firmware, fastboot never touches it):

  1. adb reboot bootloader
  2. fastboot flash boot/dtbo/init_boot/vbmeta/vendor_boot/recovery  (this script)
  3. fastboot reboot recovery
  4. on the phone: Apply update > Apply from ADB
  5. adb sideload $ZIP
  6. (optional) adb sideload the GApps zip in the same recovery session
  7. reboot to system - previous build stays on the other slot as fallback

STEPS
read -r -p "Phone in fastboot (adb reboot bootloader done)? Flash the 6 boot images now? [y/N] " a
[ "$a" = y ] || { echo "stopped before flashing"; exit 0; }
for p in boot dtbo init_boot vbmeta vendor_boot recovery; do
  fastboot flash "$p" "$PRODUCT_OUT/$p.img"
done
read -r -p "Reboot to recovery now? [y/N] " a
[ "$a" = y ] && fastboot reboot recovery
echo "Now: Apply update > Apply from ADB, then:  adb sideload $ZIP"
