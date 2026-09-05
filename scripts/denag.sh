#!/usr/bin/env bash
# Disable the "crDroid needs your help!" donation nag. Idempotent.
#
# The nag is DonateReceiver (packages/apps/crDroidSettings), declared in the
# *Settings* manifest on BOOT_COMPLETED / USER_UNLOCKED. It posts an
# IMPORTANCE_HIGH notification 30 minutes after boot and re-posts it every hour,
# forever, until you tap it; tapping sets a 30-day cooldown and it starts over.
# Flipping android:enabled to false on the receiver kills the auto-nag and
# nothing else - Settings > About > Support us still works.
#
# packages/apps/Settings is a repo project, so `repo sync` puts the nag back:
# run this after every sync (apply-all.sh does).
#
#   scripts/denag.sh            disable the receiver
#   scripts/denag.sh --revert   put it back
#   scripts/denag.sh --status    report, change nothing
set -euo pipefail
. "$(dirname "$0")/../env.sh"

SETTINGS=$AOSP_TOP/packages/apps/Settings
MANIFEST=$SETTINGS/AndroidManifest.xml
RECEIVER=com.crdroid.settings.fragments.about.DonateReceiver

mode=${1:-apply}
case "$mode" in
  apply|--apply) want=false ;;
  --revert)      want=true  ;;
  --status)      want=      ;;
  *) echo "usage: $(basename "$0") [--revert|--status]" >&2; exit 2 ;;
esac

[ -f "$MANIFEST" ] || { echo "!! $MANIFEST not found"; exit 1; }

# The receiver declaration, as two lines: android:name then android:enabled.
# Anything else means upstream restructured the block - refuse rather than guess.
state=$(perl -0ne '
  print "$1\n" if /<receiver\s+android:name="\Q'"$RECEIVER"'\E"\s*\n\s*android:enabled="(true|false)"/
' "$MANIFEST")

if [ -z "$state" ]; then
  if grep -q "$RECEIVER" "$MANIFEST"; then
    echo "!! $RECEIVER is in the manifest but not in the expected"
    echo "   '<receiver android:name=...> / android:enabled=\"...\"' shape - refusing to edit."
    echo "   Look at $MANIFEST and update this script."
    exit 1
  fi
  echo "== donate nag =="
  echo "  $RECEIVER not declared - upstream dropped it? nothing to do"
  exit 0
fi

echo "== donate nag ($MANIFEST) =="
if [ -z "$want" ]; then
  [ "$state" = false ] && echo "  receiver enabled=false - nag disabled" \
                       || echo "  receiver enabled=true - nag ACTIVE"
  exit 0
fi

if [ "$state" = "$want" ]; then
  echo "  receiver already enabled=$want - nothing to do"
  exit 0
fi

perl -0pi -e '
  s/(<receiver\s+android:name="\Q'"$RECEIVER"'\E"\s*\n\s*android:enabled=")(?:true|false)(")/${1}'"$want"'${2}/
' "$MANIFEST"

new=$(perl -0ne '
  print "$1\n" if /<receiver\s+android:name="\Q'"$RECEIVER"'\E"\s*\n\s*android:enabled="(true|false)"/
' "$MANIFEST")
[ "$new" = "$want" ] || { echo "!! edit did not take (still enabled=$new)"; exit 1; }

grep -n "$RECEIVER" -A1 "$MANIFEST" | sed 's/^/  /'
[ "$want" = false ] && echo "  nag disabled - rebuild for it to take effect" \
                    || echo "  nag restored"
