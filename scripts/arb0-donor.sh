#!/usr/bin/env bash
# Populate work/arb0-stash/ with the four ARB0 bootloader images.
#
#   scripts/arb0-donor.sh <official-ARB0-crDroid.zip>   extract from the donor OTA
#   scripts/arb0-donor.sh --import <dir>                 copy from an existing stash
#
# Either way every blob is verified against arb0/manifest.sha1 and .sha256 and
# the run fails if anything differs. The donor must itself read ARB 0 - the
# extraction path checks that with tools/arbcheck.py before copying.
set -euo pipefail
. "$(dirname "$0")/../env.sh"

[ $# -ge 1 ] || { sed -n 2,9p "$0"; exit 2; }
mkdir -p "$ARB0_STASH"

if [ "$1" = "--import" ]; then
  SRC=${2:?--import needs a directory}
  for b in $ARB0_BLOBS; do
    [ -f "$SRC/$b.img" ] || { echo "!! $SRC/$b.img missing"; exit 1; }
    cp -p "$SRC/$b.img" "$ARB0_STASH/$b.img"
  done
else
  ZIP=$1
  [ -f "$ZIP" ] || { echo "!! no such zip: $ZIP"; exit 1; }
  echo "== donor ARB check: $ZIP =="
  python3 "$REPO/tools/arbcheck.py" "$ZIP" | tail -3
  python3 "$REPO/tools/arbcheck.py" "$ZIP" >/dev/null || {
    echo "!! donor is not ARB 0 - refusing"; exit 1; }
  TMP=$(mktemp -d "$WORK/donor.XXXXXX")
  # arbswap --firmware-only extracts just the bootloader group from the donor.
  python3 "$REPO/tools/arbswap.py" --firmware-only -o "$TMP" "$ZIP" "$ZIP"
  for b in $ARB0_BLOBS; do cp -p "$TMP/$b.img" "$ARB0_STASH/$b.img"; done
  rm -rf "$TMP"
fi

echo "== verifying against arb0/manifest.* =="
( cd "$ARB0_STASH" && sha1sum -c "$REPO/arb0/manifest.sha1" && sha256sum -c "$REPO/arb0/manifest.sha256" )
for b in $ARB0_BLOBS; do
  python3 "$REPO/tools/arbcheck.py" "$ARB0_STASH/$b.img" >/dev/null 2>&1 \
    || { echo "!! $b.img does not read ARB 0"; exit 1; }
done
echo "stash ready: $ARB0_STASH"
