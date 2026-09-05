#!/usr/bin/env bash
# Re-apply the ARB0 firmware swap to the vendor tree. Idempotent.
#
# Copies the four donor blobs over vendor/oneplus/waffle/radio/ and rewrites
# their SHA1 pins in Android.mk (the build refuses a blob whose pin does not
# match - ARB.md section 8). repo sync restores the upstream ARB1 blobs and
# pins, so run this after every sync.
set -euo pipefail
. "$(dirname "$0")/../env.sh"

for b in $ARB0_BLOBS; do
  [ -f "$ARB0_STASH/$b.img" ] || {
    echo "!! $ARB0_STASH/$b.img missing - run scripts/arb0-donor.sh first"; exit 1; }
done
( cd "$ARB0_STASH" && sha1sum -c --quiet "$REPO/arb0/manifest.sha1" ) || {
  echo "!! stash does not match arb0/manifest.sha1 - refusing"; exit 1; }

echo "== ARB0 re-apply -> $VENDOR =="
for b in $ARB0_BLOBS; do
  cp -p "$ARB0_STASH/$b.img" "$VENDOR/radio/$b.img"
  sha1=$(sha1sum "$VENDOR/radio/$b.img" | cut -d' ' -f1)
  sed -i -E "s#(add-radio-file-sha1-checked,radio/$b\.img,)[0-9a-f]{40}#\1$sha1#" "$VENDOR/Android.mk"
  printf '  %-16s %s\n' "$b.img" "$sha1"
done

echo "== verify =="
ok=1
for b in $ARB0_BLOBS; do
  want=$(sha1sum "$ARB0_STASH/$b.img" | cut -d' ' -f1)
  have=$(grep -oE "radio/$b\.img,[0-9a-f]{40}" "$VENDOR/Android.mk" | cut -d, -f2)
  [ "$want" = "$have" ] || { echo "  MISMATCH $b: pin=$have want=$want"; ok=0; }
done
python3 "$REPO/tools/arbcheck.py" "$VENDOR/radio/xbl_config.img" | grep 'ARB ='
[ "$ok" = 1 ] || { echo "!! pin mismatch"; exit 1; }
echo "  all 4 pins match the donor blobs"
echo "ARB0 swap in place. Build, then scripts/preflight.sh before flashing."
