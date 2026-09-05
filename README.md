# crDroid 16 for the OnePlus 12 (waffle), self-built: ARB0 + KernelSU-Next/SUSFS + OxygenOS camera

The recipe and tooling for building your own crDroid 16 for `waffle` that

* **keeps the anti-rollback fuse at 0.** Every official crDroid build from
  v12.6 on ships an `xbl_config` that reads ARB 1; installing it, by OTA,
  sideload or "install to inactive slot", blows a QFPROM fuse and locks the
  phone out of every older firmware for good. This build swaps in the v12.5
  bootloader group at the source level, so the output is ARB 0 by construction.
* **has root that passes Play Integrity.** KernelSU-Next (`dev-susfs`) with
  SUSFS compiled into the kernel, plus NeoZygisk and AlwaysStrong on top.
  `MEETS_STRONG_INTEGRITY` verified.
* **runs the OxygenOS stock camera**, via the sibling
  [`oplus-camera-waffle`](https://github.com/ynotzort/oplus-camera-waffle) repo: photo, video,
  portrait, Live Photo, all three rear lenses.

Nothing proprietary is committed. Firmware blobs are extracted from an
official crDroid zip, SUSFS and KernelSU-Next are cloned at pinned commits,
the camera blobs come from an OxygenOS OTA.

**Read `docs/ROM.md` section 14 before updating an existing installation.**
The normal update paths for this device are all traps.

---

## What is in here

```
versions.env          every upstream pin: crDroid branch, KSU-Next + SUSFS commits,
                      the ARB0 donor build, GApps
env.sh                path resolution ($ANDROID_BUILD_TOP / $AOSP_TOP / /aosp)
scripts/
  apply-all.sh        ksu + arb0 + camera in one go - run after every repo sync
  ksu-reapply.sh      KernelSU-Next + SUSFS into kernel/oneplus/sm8650 (pinned; --latest to move)
  arb0-donor.sh       extract the 4 ARB0 blobs from the donor zip into work/, verify hashes
  arb0-reapply.sh     swap them into vendor/oneplus/waffle and rewrite the SHA1 pins
  camera-apply.sh     the camera repo's tree edits (frameworks/*, hardware/oplus, device.mk)
  denag.sh            disable the "crDroid needs your help!" donation notification
  status.sh           what state is the tree in (read-only)
  build.sh            brunch with ccache, logged to work/logs/, preflight at the end
  preflight.sh        THE GATE: ARB 0 + KSU in the packaged kernel, or it refuses
  flash-update.sh     dirty flash: 6 boot images + sideload, each step confirmed
tools/
  arbcheck.py         read the Qualcomm ARB field out of a zip / payload.bin / .img
  arbswap.py          build an ARB0 image set from an ARB1 ROM + ARB0 donor
arb0/                 SHA-1 and SHA-256 of the donor blobs (not the blobs)
docs/
  ROM.md              the build journal: host, OOM, ARB0 build, flashing, KSU, updates, camera
  ARB.md              the anti-rollback analysis: where the field lives, why it cannot be patched
  MODULES.md          on-device modules, Manager, GApps - versions and hashes
work/                 git-ignored: clones, blob stash, logs, copies of flashed builds
```

All of it is idempotent. Everything the scripts change in the Android tree is
an **uncommitted working-tree edit** in five projects (`vendor/oneplus/waffle`,
`kernel/oneplus/sm8650`, `device/oneplus/sm8650-common`,
`packages/apps/Settings`, and the camera's five). `repo sync` reverts them -
that is why `apply-all.sh` exists and why you never pass `--force-sync` to a
sync of a tree you care about.

---

## First build

### 0. Host

Tested on Fedora 44, 8c/16t, 31 GiB RAM. Expect 4-8 h for the first build.
`docs/ROM.md` sections 2-4 have the package list and the filesystem notes
(btrfs subvolume, `nodatacow`, external NTFS is not usable). Two things bite:

* **`soong_build` peaks near 28 GiB RSS.** On a 32 GiB host you need a real
  disk swapfile of ~32 GiB; zram does not cover it and both `systemd-oomd`
  and the kernel OOM killer will kill the build otherwise. Section 7.
* **Nothing with an `Android.mk` may live under the build root.** The
  susfs4ksu clone has one; kati picked it up and failed the build. `work/`
  is outside the tree for that reason.

### 1. Sync crDroid

```bash
mkdir /aosp && cd /aosp          # or anywhere; export ANDROID_BUILD_TOP
repo init -u https://github.com/crdroidandroid/android.git -b 16.0 \
          --git-lfs --no-clone-bundle --depth=1
repo sync -c --no-clone-bundle --no-tags -j8
source build/envsetup.sh
breakfast waffle                 # fails once with "Cannot locate config makefile" - normal;
                                 # roomservice clones the 11 device/kernel/vendor repos on that pass
```

`--git-lfs` is required: the firmware images in the TheMuppets vendor tree are
LFS objects, and `xbl_config.img` is one of them.

### 2. Clone this repo and the camera repo side by side

```bash
cd ~/src
git clone https://github.com/ynotzort/crdroid-waffle-arb0
git clone https://github.com/ynotzort/oplus-camera-waffle    # or set OPLUSCAM_REPO
```

### 3. Get the ARB0 donor blobs

You need the official **crDroid 16.0 v12.5** zip for waffle (2026-01-01), the
newest build whose firmware is still ARB 0. Then:

```bash
scripts/arb0-donor.sh ~/Downloads/crDroidAndroid-16.0-20260101-waffle-v12.5.zip
```

It checks the donor reads ARB 0, extracts `abl xbl xbl_config xbl_ramdump`
into `work/arb0-stash/`, and verifies each against `arb0/manifest.*`. If you
already have a verified stash, `scripts/arb0-donor.sh --import <dir>`.

### 4. Apply the three integrations

```bash
scripts/apply-all.sh
```

which runs `ksu-reapply.sh` (clones KernelSU-Next into the kernel tree at the
pinned commit, susfs4ksu into `work/`, copies the SUSFS sources, applies the
`50_` patch, writes `ksu.config` and wires it into `TARGET_KERNEL_CONFIG`),
`arb0-reapply.sh` (copies the four blobs over `vendor/oneplus/waffle/radio/`
and rewrites their SHA-1 pins in `Android.mk`, without which kati refuses the
build), and `camera-apply.sh` (the camera repo's `treepatch.py apply`), then
prints `status.sh`.

### 5. Build, gate, flash

```bash
scripts/build.sh                 # brunch waffle; runs preflight.sh on success
```

Only if it prints **PREFLIGHT PASS** do you flash. For a first install of a
self-built ROM over an official one the recovery has to change too, and the
data has to be formatted (different signing keys). `docs/ROM.md` section 12:
fastboot the six boot images to the active slot, `fastboot reboot recovery`,
Format data, Apply from ADB, `adb sideload` the zip, then GApps in the same
session. No `flashing unlock_critical` is needed - `update_engine` writes the
firmware, fastboot never does.

Then install the KernelSU-Next Manager, the SUSFS module, NeoZygisk and
AlwaysStrong (`docs/MODULES.md`, in that order), and the camera module from
the camera repo.

---

## Updating later

```bash
cd $ANDROID_BUILD_TOP
repo sync -c --no-clone-bundle --no-tags -j8      # NO --force-sync: let it report conflicts
scripts/apply-all.sh                              # idempotent
scripts/build.sh                                  # ends with preflight
scripts/flash-update.sh                           # dirty flash, every step confirmed
```

Same test-keys, so no format: data, modules, keybox and root grants survive.
The previous build stays on the other slot. Two judgement calls recur:

* **crDroid bumps the firmware base.** `arb0-reapply.sh` always forces the
  v12.5 bootloader group in. If a future release moves to a much newer
  `tz`/`hyp`/`devcfg`, a v12.5 `xbl` against them is untested; keep an
  MSM/EDL package ready on any update that changes non-swapped firmware.
  `tools/arbcheck.py <official zip>` tells you whether `xbl_config` is still
  the only ARB1 image.
* **SUSFS and the KSU-Next driver drift apart.** The pins in `versions.env`
  are a matched pair. `ksu-reapply.sh --latest` moves both to branch heads
  and prints the commits; if the kernel then fails on a `susfs_*` signature
  (`core_hook.c: assigning to 'int' from incompatible type 'void'`), the
  driver is behind SUSFS - wait a day or pin SUSFS back.

---

## Why these choices

* **`dev-susfs`, not `next-susfs`.** Every tutorial names `next-susfs`. It
  stopped in November 2025, no longer compiles against current SUSFS, and
  numbers itself so that every current Manager APK rejects it. `dev-susfs`
  is v3.3.0-based and works with the official Manager. Upstream KernelSU-Next
  has no SUSFS at all. `docs/ROM.md` section 13.
* **Swap the whole bootloader group, not just `xbl_config`.** `xbl` and
  `xbl_config` are a matched pair (DDR training, clocks, PMIC), and every
  image differs between v12.5 and v12.11. `docs/ARB.md` sections 5 and 8.
* **The ARB byte cannot be patched.** It sits under the OEM ECDSA P-384
  signature; flipping it gives you an image XBL refuses. Only a genuinely
  OnePlus-signed ARB0 image works. `docs/ARB.md` section 5.
* **Camera tree edits are managed by the camera repo, not duplicated here.**
  Six marker-delimited edits and one added file, with a revert that verifies
  against `git HEAD`. This repo only calls them.

## Licence

Apache-2.0 for the scripts and docs. crDroid, KernelSU-Next, SUSFS and the
OnePlus firmware and camera blobs are their own projects and are not
redistributed here.
