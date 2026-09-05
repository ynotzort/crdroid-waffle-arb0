> Journal copied verbatim from the working notes on 2026-09-05. Paths like `/aosp`
> and `~/Downloads/OP12` are the author's machine; the repo scripts under `scripts/`
> are the portable form of what section 14 describes. Read this for the *why*.

# Building crDroid 16 for OnePlus 12 (waffle) from source

Companion to [ARB.md](ARB.md). That document covers the anti-rollback fuse; this one
covers building the ROM yourself, which is also the cleanest way to avoid the fuse
(see ARB.md section 8).

Started 2026-08-29.

---

## 1. Goal

Build crDroid 16 for `waffle` from source, compare the result against the downloaded
`crDroidAndroid-16.0-20260809-waffle-v12.11.zip`, and ultimately produce an ARB0 build
with KernelSU Next integrated.

**First build is stock** — no modifications. If the build breaks, the problem should be
crDroid's tree, not our patches.

---

## 2. Build host

| | |
|---|---|
| CPU | Intel i7-11800H, 8 cores / 16 threads |
| RAM | 31 GiB + 8 GiB swap |
| OS | Fedora 44, Linux 7.1.9 |
| Build dir | `/aosp` (btrfs subvolume on internal NVMe) |

Expect roughly 4-8 h for a first clean build on this machine.

---

## 3. Storage: why `/aosp` and not the external drive

The first candidate was `/run/media/w/Ventoy/OP12` on a Samsung T7 Touch (1.8 TB USB,
534 GB free). It was rejected after measurement.

### The drive is NTFS, not exFAT

Mount helper is `/sbin/mount.ntfs` with `windows_names`. exFAT would not have passed the
symlink and exec-bit tests below, so this is ntfs-3g.

### AOSP filesystem requirements — all pass except one

| Requirement | Result |
|---|---|
| Case-sensitive filenames | yes |
| Symlinks | yes |
| Hardlinks | yes |
| Executable bit | yes |
| Colon in filename | **NO** - blocked by the `windows_names` mount option |

So it is not a semantic blocker. It is a performance blocker.

### Measured throughput

| | internal NVMe | Ventoy T7 (ntfs-3g) |
|---|---|---|
| Bulk write (1 GiB, fsync) | **2.0 GB/s** | **45.2 MB/s** |
| Cold sequential read | - | 169 MB/s |
| 1500 small files: create | 0.04 s | 0.19 s |
| 1500 small files: read | 0.81 s | 0.88 s |
| `git clone` (device tree) | 1.08 s | 1.67 s |

Small-file metadata work is only mildly slower - the page cache absorbs most of it. Bulk
write is **45x slower**, and that is what kills it: a repo sync (~100 GB) plus a build
(~150 GB of intermediates) is ~1.5 h of *pure sequential write time* before any random
I/O or fsync overhead.

The hardware is not the problem. A T7 Touch does ~1 GB/s over USB 3.2; ntfs-3g's
single-threaded FUSE layer is the bottleneck.

### Where the build actually lives

```
/dev/nvme0n1p3 -> LUKS -> btrfs  /      476 GB, 425 GB free, 2.1 GB/s
/dev/nvme1n1p1 -> LUKS -> ext4   /home  932 GB, 283 GB free, 2.0 GB/s
```

`/` had the space, so the build tree is `/aosp`.

### Two btrfs details that matter

**snapper runs hourly timeline snapshots on `/`.** A 250 GB build tree that rewrites
intermediates constantly would have every rewritten extent pinned by snapshots; the
volume fills and `snapper-cleanup` cannot keep up. Btrfs snapshots do **not** descend
into nested subvolumes, so the build tree is its own subvolume:

```bash
sudo btrfs subvolume create /aosp     # NOT mkdir - a subvolume escapes snapshots
sudo chown w:w /aosp
chattr +C /aosp                       # nodatacow; must be set while empty, inherited
```

`chattr +C` disables copy-on-write, which otherwise fragments a churning `out/` badly.
It also disables the volume's `compress=zstd:1` for those files - fine for build
output, and it saves CPU.

Verify it really is a subvolume (subvolume roots always have inode 256):

```bash
stat -c %i /aosp    # 256
lsattr -d /aosp     # ---------------C------
```

---

## 4. Host dependencies (Fedora 44)

Already present: `git curl unzip zip rsync bc bison flex ccache python3 make m4 lz4
zstd openssl xmllint gawk tar`, plus `repo` 2.65.

Installed for the build:

```bash
sudo dnf install -y gperf libxcrypt-compat ncurses-compat-libs zlib-devel
```

Note: on Fedora 44 `rpm -q zlib-devel` reports missing even when the headers are
present - it is provided by **`zlib-ng-compat-devel`**. Check for the header, not the
package name:

```bash
ls /usr/include/zlib.h
```

`git config --global user.name` / `user.email` must be set or `repo init` prompts.

---

## 5. What a self-build can and cannot reproduce

**A self-build cannot be byte-identical to the official zip.**

| | Why |
|---|---|
| Signing keys | crDroid signs official builds with private release keys. A self-build uses `test-keys` unless you generate your own. Changes every APK and the OTA signature. |
| Build metadata | `ro.build.date`, build number, hostname and username are baked into the props. |
| Source revisions | v12.11 was built 2026-08-09; the `16.0` branches have moved since. crDroid publishes no per-build manifest snapshot, so that exact tree state is not recoverable. |

What is worth comparing instead:

- the payload contains the same 40 partitions
- partition sizes are in the same ballpark
- device fingerprint props match (hardcoded in `lineage_waffle.mk`, so they should)
- **ARB status** - the real prize, see ARB.md section 8

---

## 6. Procedure

```bash
cd /aosp
repo init -u https://github.com/crdroidandroid/android.git -b 16.0 \
          --git-lfs --no-clone-bundle --depth=1
repo sync -c --no-clone-bundle --no-tags --force-sync -j8
```

`--git-lfs` is **required**: the firmware blobs (including `xbl_config.img`) are Git LFS
objects in the TheMuppets vendor tree. `--depth=1` matters too - full history for this
manifest is well over 100 GB, and the kernel repo alone carries 1.19M commits.

```bash
source build/envsetup.sh
breakfast waffle       # roomservice resolves crdroid.dependencies
brunch waffle
```

### `breakfast` fails on the first pass - this is normal

The first `breakfast waffle` ends with:

```
build/make/core/product_config.mk:226: error: Cannot locate config makefile
    for product "lineage_waffle".
dumpvars failed with: exit status 1
```

followed by a Go stack trace from Soong. This is **not** a real failure. `breakfast`
looks up the product first, does not find it because the device tree has not been
cloned yet, and only then hands off to roomservice, which writes
`.repo/local_manifests/roomservice.xml` and syncs the repos. The alarming Soong panic is
just the failed lookup that triggered the clone.

roomservice then resolves the dependency graph **recursively in that one pass** - it
reads `device/oneplus/waffle/crdroid.dependencies`, follows it to `sm8650-common`, and
follows that to the kernel repos, writing all of them into `roomservice.xml` and syncing
them together. A single `breakfast waffle` was enough here; no second pass was needed.

The resulting `roomservice.xml` had 11 projects: the two device trees, the three kernel
trees, `hardware/oplus`, `LunarisDolby`, and the four vendor trees (waffle,
sm8650-common, dolby, ir). After that, `breakfast waffle` configures the product
cleanly and `brunch waffle` becomes meaningful.

`breakfast waffle` clones, per `crdroid.dependencies`:

| Repo | Path |
|---|---|
| `crdroidandroid/android_device_oneplus_waffle` | `device/oneplus/waffle` |
| `crdroidandroid/android_device_oneplus_sm8650-common` | `device/oneplus/sm8650-common` |
| `crdroidandroid/android_kernel_oneplus_sm8650` | `kernel/oneplus/sm8650` |
| `crdroidandroid/android_kernel_oneplus_sm8650-devicetrees` | `kernel/oneplus/sm8650-devicetrees` |
| `crdroidandroid/android_kernel_oneplus_sm8650-modules` | `kernel/oneplus/sm8650-modules` |
| `crdroidandroid/android_hardware_oplus` | `hardware/oplus` |
| `TheMuppets/proprietary_vendor_oneplus_waffle` (`lineage-23.2`) | `vendor/oneplus/waffle` |
| `TheMuppets/proprietary_vendor_oneplus_sm8650-common` (`lineage-23.2`) | `vendor/oneplus/sm8650-common` |

ccache is worth enabling, pointed **outside** the snapshotted home:

```bash
export USE_CCACHE=1
export CCACHE_EXEC=$(command -v ccache)
export CCACHE_DIR=/aosp/.ccache
ccache -M 50G
```

---

## 7. Build attempt 1: killed by systemd-oomd

The first `brunch waffle` died ~3 minutes in. It was **not** a build error.

```
Aug 30 00:28:59 systemd-oomd[1108]: Killed
  /user.slice/user-1000.slice/user@1000.service/app.slice/
  app-ghostty-transient-5139.scope/surfaces/2C372D20.scope
  due to memory pressure for /user.slice/user-1000.slice/user@1000.service
  being 81.06% > 80.00% for > 20s with reclaim activity
  Current Memory Usage: 20.5G
```

### What actually happened

`build.log` stops at `[99% 283/284] cp out/host/linux-x86/bin/soong_build` and shows no
error, which looks like a silent crash at the end of the Soong bootstrap. It is not.
The bootstrap finished; the **`soong_build` analysis phase** then ran for ~3 minutes
producing no log output at all (it loads the whole module graph in one process and
prints nothing). The journal shows `soong_build` alive from 00:26 to 00:29 with memory
pressure climbing the whole time:

```
mem_s   0.32 -> 2.19 -> 5.92 -> 17.56 -> 47.55 -> 73.44 -> 87.63 -> 93.55
```

with `kswapd0` blocking and the CPU at 100 C throttling hard. Then oomd killed the
cgroup.

### Two things worth knowing

**`setsid nohup` does not protect a process from oomd.** `setsid` starts a new *session*;
it does not move the process into a new *cgroup*. The build stayed in the terminal
emulator's scope (`app-ghostty-transient-*.scope`), so when oomd killed that scope for
being the highest-pressure cgroup, it took the build with it. Detaching is not isolation.

**A missing "build end" line is the tell.** `build.sh` ends with an unconditional
`echo "=== build end ... rc=$rc ==="`. Its absence proves the script was killed rather
than having exited, which is what pointed at an external killer rather than a build
failure.

### Why the memory ran out

31 GiB total, with ~12 GiB already held by the desktop, Firefox and other apps. AOSP 16's
`soong_build` analysis wants well over 10 GiB on its own; the scope peaked at 20.5 GiB.
Swap is only 8 GiB, so there was nowhere to spill and pressure went vertical.

Note that lowering `-j` does **not** help this phase: `soong_build` analysis is a single
process, not a parallel compile. Parallelism only matters for the ninja phase afterwards.

### Attempt 2: the kernel OOM killer, and the real diagnosis

Firefox was closed (freeing ~5 GiB, 24 GiB available) and the build was relaunched as a
transient systemd service (`systemd-run --user --unit=aospbuild`) so it had a cgroup of
its own instead of living inside the terminal's scope. It died again after 1m44s, this
time to a different killer:

```
oom-kill: constraint=CONSTRAINT_NONE, global_oom,
          task_memcg=/user.slice/.../app.slice/aospbuild.service, task=soong_build
Out of memory: Killed process 3888982 (soong_build)
          total-vm:37733708kB, anon-rss:27583440kB
aospbuild.service: Consumed 15min 47.253s CPU time over 1min 44.014s wall clock time,
          26.3G memory peak, 3G memory swap peak
```

`global_oom` - the **kernel** OOM killer, not systemd-oomd. No oomd entry exists for this
one.

**This is the real diagnosis.** `soong_build` for this tree peaks at ~27.5 GiB RSS on a
31 GiB machine. Attempt 1's oomd kill was a *symptom*: oomd was simply the first thing to
notice a machine running out of memory. Turning oomd off would not have saved attempt 1;
the kernel would have killed it a few seconds later, which is exactly what happened in
attempt 2.

### Two measurement lessons

**zram is not swap for this purpose.** The 8 GiB of swap on this machine is `/dev/zram0` -
compressed pages held *in RAM*. It buys some effective capacity but cannot rescue a
genuine overcommit; the unit still only managed a 3 GiB swap peak before dying.

**Do not sample `MemoryCurrent` on a slow interval.** A watcher polling every 60s reported
a 17.76 GiB peak for a phase that lasted 104 seconds - it simply missed the spike. Use
systemd's own accounting, which is exact:

```bash
systemctl --user show aospbuild -p MemoryPeak -p Result
# or, after the fact, the "N memory peak" line systemd logs on unit stop
```

### Fix

The `soong_build` peak is not tunable - it is one process loading the whole module graph,
so `-j` does not touch it. The only options are more memory or more swap. Real disk swap
is required:

```bash
# btrfs swapfiles must be nodatacow and uncompressed:
# truncate to zero, chattr +C, THEN allocate. /aosp is already a nodatacow subvolume.
sudo truncate -s 0 /aosp/swapfile
sudo chattr +C /aosp/swapfile
sudo fallocate -l 32G /aosp/swapfile
sudo chmod 600 /aosp/swapfile
sudo mkswap /aosp/swapfile
sudo swapon /aosp/swapfile
```

32 GiB gives ~55 GiB total against a ~27.5 GiB peak, with room for the ninja phase after
it. Swap sits on the 2 GB/s NVMe, so paging is tolerable.

**This swap is not persistent.** `swapon` activates it for the current boot only; nothing
is written to `/etc/fstab`, so a reboot removes it and leaves the 32 GiB file sitting on
disk doing nothing. That is deliberate - it is a build-time change, not a permanent
reconfiguration of the machine. To make it survive reboots anyway:

```bash
echo '/aosp/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab
```

Verify it is active either way:

```bash
swapon --show      # expect /aosp/swapfile, TYPE file, SIZE 32G
free -h            # total swap should read ~40Gi (32 GiB file + 8 GiB zram)
```

Optionally, to stop oomd interfering while the machine swaps heavily:

```bash
sudo mkdir -p /etc/systemd/system/user@1000.service.d
printf '[Service]\nManagedOOMMemoryPressure=off\n' \
  | sudo tee /etc/systemd/system/user@1000.service.d/no-oomd.conf
sudo systemctl daemon-reload
```

This is secondary. The swapfile is the fix.

## 8. Build attempt 3: success

With the 32 GiB swapfile active, the same build ran to completion.

```
crDroidAndroid-16.0-20260830-waffle-v12.11.zip   3.0 GB   rc=0
build time ~5h45m (08:37 -> 14:23), -j12, ccache cold (7.8 GB written)
out/ peaked at ~143 GB; disk never fell below 144 GB free
```

`soong_build` cleared its ~26 GiB peak by paging ~10-14 GiB to the swapfile, then the
ninja phase ran steadily with 19-22 GiB available throughout. Swap did its job only
during analysis; the compile phase never needed it.

### Comparison against the official v12.11 zip

Identical structure:

| | built (20260830) | official (20260809) |
|---|---|---|
| partitions | 40 | 40 |
| total size | 8.06 GiB | 8.06 GiB |
| partitions only in one | none | none |
| build fingerprint | `OnePlus/CPH2573IN/OP595DL1:16/BP2A.250605.015/U.R4T3.1bb7df3_9001b7_8f80ba:user/release-keys` | **identical** |
| security patch level | 2026-08-01 | **identical** |
| `pre-device` | `OP5929L1,OP595DL1` | **identical** |
| `post-timestamp` | 1788071845 | 1786278105 |

**37 of 40 partitions match byte-for-byte in size.** The three that differ are all AOSP
partitions, and by trivial margins - 20 days of upstream commits:

| partition | delta | % |
|---|---|---|
| `product` | -2,232,320 | 0.08% |
| `system` | -12,288 | 0.001% |
| `system_ext` | +32,768 | 0.002% |

**All 18 signed Qualcomm firmware partitions are bit-identical** (SHA-256 compared):
`xbl xbl_config xbl_ramdump abl tz hyp devcfg aop uefi keymaster cpucp shrm qupfw
imagefv uefisecapp aop_config featenabler oplus_sec`. That is expected and confirms the
mechanism from ARB.md section 8 - the build copies prebuilt vendor blobs through
untouched rather than generating them.

### ARB confirmed

The built ROM reads **ARB = 1**, exactly like the official one. Its `xbl_config.img` is
`sha256 bd086baa...f5fda38b` - the same bytes as the vendor tree blob and as the official
zip's `xbl_config`. This is the intended baseline result: a stock self-build is no safer
than the official ROM, and the fix is the one-file swap.

### What could not be compared

Byte-identity of the whole zip, for the reasons in section 5: this build is signed with
`test-keys`, the official one with crDroid's private release keys, and `post-timestamp`
necessarily differs. The comparison above is the meaningful one.

## 9. Build attempt 4: the ARB0 build

After swapping the v12.5 bootloader group into the vendor tree and recomputing the SHA1
pins, the rebuild succeeded in ~15 minutes (most targets cached).

```
crDroidAndroid-16.0-20260830-waffle-v12.11.zip   3.0 GB   rc=0
ARB = 0  -  safe, this will not blow the anti-rollback fuse
```

All 20 signed firmware images in the payload read `anti_rollback_version = 0`.

### Diff against the ARB1 baseline

Comparing the two builds partition-by-partition (SHA-256 read straight from
`new_partition_info` in the payload manifest - no need to decompress 8 GiB twice):

**24 of 40 partitions bit-identical**, including every firmware image that was *not*
swapped: `aop aop_config bluetooth cpucp cpucp_dtb devcfg dsp featenabler hyp imagefv
keymaster modem oplus_sec oplusstanvbk qupfw shrm splash tz uefi uefisecapp`, plus
`boot`, `dtbo`, `vendor_boot`, `engineering_cdt`.

**The 4 intended swaps**, each now matching the v12.5 blob:

| partition | ARB1 baseline | ARB0 build |
|---|---|---|
| `xbl` | `a949285e72c4` | `7104356d77b9` |
| `xbl_config` | `bd086baa2cc1` | `9ca6e36eee0a` |
| `xbl_ramdump` | `a09bab3c4ff1` | `4cfe2d30058d` |
| `abl` | `69e54880c431` | `8a3ce967e98c` |

The baseline values are byte-identical to the **official** v12.11 zip's firmware,
confirming the baseline was a faithful reproduction before the swap.

### The 12 other partitions that changed - and why that is fine

`system system_ext system_dlkm product vendor vendor_dlkm odm init_boot recovery
vbmeta vbmeta_system vbmeta_vendor` also differ. This is **not** a side effect of the
swap - it is ordinary AOSP non-reproducibility:

```
baseline post-timestamp   1788071845   (08:37:25)
arb0     post-timestamp   1788102805   (17:13:25)
ro.build.date=Sun Aug 30 17:13:25 CEST 2026     <- matches the second build exactly
```

`ro.build.date` and `ro.build.date.utc` are baked into `build.prop`, which lives inside
`system`, `product`, `vendor`, `odm`, `system_ext` and the dlkm images; `init_boot` and
`recovery` carry ramdisks with prop files; and the `vbmeta*` images chain hashes of
everything above, so they follow. The build fingerprint is **identical** across both
builds - only the timestamp moved.

Two builds of AOSP at different times are never bit-identical without a dedicated
reproducible-build setup. `boot`, `dtbo` and `vendor_boot` happen not to embed the
timestamp, which is why they stayed identical.

## 10. Kernel facts (for the later KernelSU Next work)

- Built **from source**, not prebuilt: `TARGET_KERNEL_SOURCE := kernel/oneplus/sm8650`
- Linux **6.1.175**, GKI (`BOARD_USES_GENERIC_KERNEL_IMAGE := true`)
- Configs: `gki_defconfig` + `vendor/pineapple_GKI.config` + `vendor/oplus/pineapple_GKI.config`
- `CONFIG_KPROBES=y` is already set in `gki_defconfig` - kprobe-mode KSU should work
- No `drivers/kernelsu` in the tree today
- External modules from `kernel/oneplus/sm8650-modules` (qcom camera/audio/graphics/bt/...)

---

## 11. Flashing: device state and the critical-partition block

Attempted 2026-08-30. **Nothing was written to the phone.** The first write was rejected
by the bootloader and the run stopped there, so the device is in its original state.

### The device

```
OnePlus 12 CPH2581 (EEA), OP595DL1, serial 5705405b
running crDroid v12.5-20260111  ->  ARB 0  (consistent with ARB.md section 4)
fastboot: unlocked=yes  secure=yes  current-slot=a  slot-count=2  product=pineapple
```

### `getprop` lies about the lock state - do not trust it

The running system reports the device as **locked**:

```
ro.boot.flash.locked        = 1
ro.boot.vbmeta.device_state = locked
ro.boot.verifiedbootstate   = green
ro.boot.veritymode          = enforcing
sys.oem_unlock_allowed      = 0
```

All of that is **false**. The phone displays the orange-state warning at boot, which is
drawn by the bootloader itself and cannot be spoofed by the OS, and `fastboot getvar
unlocked` returns `yes`. crDroid (or a Magisk module) rewrites these properties so Play
Integrity passes.

The kernel-supplied values that would settle it are unreadable without root:

```
$ adb shell cat /proc/cmdline
cat: /proc/cmdline: Permission denied      # SELinux, Android 12+
$ adb shell cat /proc/bootconfig
cat: /proc/bootconfig: Permission denied
```

**Only `fastboot getvar unlocked` is authoritative.** Never conclude a device is locked
from `getprop`.

### `has-slot` is not implemented for firmware partitions

`arbswap.py`'s generated `flash.sh` asks the device which partitions are slotted:

```bash
fastboot getvar has-slot:boot        -> yes
fastboot getvar has-slot:modem       -> yes
fastboot getvar has-slot:xbl         -> FAILED (remote: 'GetVar Variable Not found')
fastboot getvar has-slot:xbl_config  -> FAILED
fastboot getvar has-slot:abl         -> FAILED
fastboot getvar has-slot:tz          -> FAILED
```

But the slotted partitions plainly exist:

```bash
fastboot getvar partition-size:xbl_a         -> 0x600000
fastboot getvar partition-size:xbl_b         -> 0x600000
fastboot getvar partition-size:xbl_config_a  -> 0x4B000
fastboot getvar partition-size:xbl_config_b  -> 0x4B000
```

**This is a bug in `arbswap.py`.** Its `flash_both()` helper treats a `has-slot` failure
as "not slotted" and falls back to a single unslotted `fastboot flash xbl_config`, which
writes only the active slot - leaving the ARB1 image on slot `b` and defeating the entire
point of the both-slots policy. It fails silently: every command reports success.

The fix is to probe `partition-size:<part>_a` / `_b` instead of `has-slot`, and treat a
partition as slotted when both suffixed names report a size. **`arbswap.py` has not been
corrected yet.**

### Corrected slot policy

A first ad-hoc plan wrote *every* partition that had `_a`/`_b` to both slots. That is
wrong: the logical partitions in `super` are only updated for the active slot, so writing
`boot`/`vbmeta`/`recovery` to both slots would leave slot `b` with a new kernel next to an
old `system` - less bootable than leaving it untouched. The policy that was actually used:

| group | count | target |
|---|---|---|
| Qualcomm firmware (`abl aop aop_config bluetooth cpucp cpucp_dtb devcfg dsp featenabler hyp imagefv keymaster modem oplus_sec qupfw shrm tz uefi uefisecapp xbl xbl_config xbl_ramdump`) | 22 | **both slots** |
| AOSP boot images (`boot init_boot vendor_boot dtbo recovery vbmeta vbmeta_system vbmeta_vendor splash engineering_cdt oplusstanvbk`) | 11 | active slot only |
| logical (`odm product system system_dlkm system_ext vendor vendor_dlkm`) | 7 | fastbootd, active slot |

55 phase-1 writes, 7 phase-2 writes. Every image was checked against its
`partition-size` first; all fit.

### The blocker

```
Sending 'abl_a' (272 KB)   OKAY  [0.011s]
Writing 'abl_a'            FAILED (remote: 'Flashing is not allowed for Critical Partitions.')
```

The Qualcomm boot chain is protected by a **separate critical-partition lock**, distinct
from the ordinary `fastboot flashing unlock` this device already has. Writing it requires
`fastboot flashing unlock_critical`, which has **not** been run - its wipe behaviour on
this device is unverified.

The data was sent but the write was refused, so `abl_a` is unchanged.

### Options from here

1. **`fastboot flashing unlock_critical`, then retry.** Unblocks the firmware writes.
   Unknown whether it wipes data on this model - verify before running.
2. **Flash only the non-critical partitions.** `boot`, `dtbo`, `vendor_boot`,
   `init_boot`, `recovery`, `vbmeta*` and the logical partitions are not critical, so the
   v12.11 userspace could be flashed while the existing **v12.5 firmware stays in place**
   - which is already ARB 0. No critical unlock, no ARB risk, no wipe. The trade-off is
   v12.11 vendor HALs running against v12.5 firmware, which is untested.
3. **Do nothing.** The phone is on v12.5 at ARB 0 and is not at risk.

Note that option 2 inverts the problem this project set out to solve: if critical
partitions cannot be written at all, the ARB1 firmware cannot be flashed either, so the
fuse cannot blow.

### Aside: a zsh word-splitting bug

The first attempt to generate the plan produced one nonsense command, because zsh does
not word-split unquoted variables the way bash does - `for p in $FW` iterated once over
the whole string. Loops of that shape must run under `bash`, or use `${=FW}` in zsh.

## 12. The official install path — and why it sidesteps the blocker

Source: <https://crdroid.net/waffle/12/install>. Read against section 11, this resolves
the critical-partition wall.

### What crDroid actually tells you to do

```bash
adb -d reboot bootloader
fastboot flash boot        boot.img
fastboot flash dtbo        dtbo.img
fastboot flash init_boot   init_boot.img
fastboot flash vbmeta      vbmeta.img
fastboot flash vendor_boot vendor_boot.img
fastboot flash recovery    recovery.img
# reboot to recovery, then:
adb -d sideload crdroid.zip      # fresh install: "Factory Reset" > "Format data" first
adb -d sideload gapps.zip        # after a second reboot to recovery
```

Prerequisites it states: bootloader unlocked, Google accounts removed (FRP), and the
device already running **Android 16** firmware.

### The key observation

**The official procedure never fastboot-flashes a critical partition.** The six images
in step 2 — `boot dtbo init_boot vbmeta vendor_boot recovery` — are exactly the set that
sits *outside* the Qualcomm boot chain. `xbl`, `xbl_config`, `abl`, `tz` and friends are
never named. That is why the documented flow works on an ordinary
`fastboot flashing unlock` device and why `unlock_critical` is never mentioned.

The firmware still gets written — crDroid is a full-firmware OTA (ARB.md section 5,
40 partitions including the whole boot chain) — but it is written by **`update_engine`
during the sideload**, straight to `/dev/block/by-name/*`. The
`Flashing is not allowed for Critical Partitions` refusal from section 11 is a policy
enforced by **abl's fastboot implementation only**. It is not a hardware write-protect
and update_engine is not subject to it.

So the section 11 blocker was an artefact of choosing the fastboot route. The
partition-by-partition fastboot plan is the thing that needs `unlock_critical`; the
sideload route does not.

### What this means for the ARB0 build

Sideloading `crDroidAndroid-16.0-20260830-waffle-v12.11.zip` (the section 9 ARB0 build)
writes the ARB0 firmware set that fastboot refused, with no `unlock_critical` and no
special slot handling:

- update_engine writes to the **inactive** slot and then switches to it. The device is
  on slot `a` running v12.5 (ARB 0), so the ARB0 build lands on slot `b`.
- Both slots therefore end up ARB 0 — slot `a` keeps v12.5, slot `b` gets the swapped
  set. The both-slots policy of section 11 is satisfied for free, and the
  `has-slot` bug in `arbswap.py` stops mattering for this route.
- The `dtbo`/`vbmeta`/`vendor_boot`/`boot`/`recovery` writes in step 2 go to the active
  slot `a`, which is the ordinary crDroid flow.

### Two things that do not carry over

**Use this build's own images, not the official ones.** Recovery verifies the sideloaded
zip against the certificate in its own ramdisk. This build is signed with `test-keys`
(section 5), so its zip only verifies under **its own** `recovery.img`. Flashing
crDroid's official recovery and then sideloading the self-build would be rejected;
mixing the two in either direction fails. All six step-2 images must come from
`/aosp/out/target/product/waffle/`.

**A format data is required, and section 11's "no wipe" no longer holds.** The device
currently runs crDroid signed with crDroid's private release keys; this build is signed
with `test-keys`. Platform-signed system apps change signature across that boundary,
which is not a dirty-flashable transition. Take the "Format data" branch of step 3.
The no-wipe flash set prepared on 2026-08-30 was a property of the fastboot route only.

### Revised options from section 11

| | route | ARB | wipe | needs `unlock_critical` |
|---|---|---|---|---|
| 1 | sideload the **ARB0 self-build** | 0 | yes (key change) | no |
| 2 | sideload the **official v12.11** | **1 — fuse blows** | yes | no |
| 3 | fastboot mixed set (section 11) | 0 | no | **yes, unverified** |
| 4 | do nothing, stay on v12.5 | 0 | - | no |

Option 2 is the trap: the documented, supported, official procedure is precisely the one
that blows the fuse. Option 1 is the same procedure with a zip whose `xbl_config` reads
ARB 0.

A fifth route exists if the key change is unacceptable: regenerate the package as a
**partial OTA** excluding every firmware partition
(`ota_from_target_files --partial "system system_ext product vendor odm ..."`), leaving
the device's existing v12.5 ARB0 firmware in place. `--partial` is supported by the
in-tree releasetools. It still does not solve the signing-key wipe, so it only helps if
the goal is to avoid touching firmware at all.

### Outcome - flashed 2026-08-30

Executed exactly as option 1 above. No `unlock_critical`, no critical-partition refusal.

```
step 2   6 images from /aosp/out/... to slot a      all OKAY
recovery boots our build, accepts our zip
format   Factory Reset > Format data
sideload crdroid-arb0.zip   Total xfer: 1.00x   rc=0
slot     current-slot a -> b, unbootable:b no
boot     OK, ro.build.date.utc = 1788102805  (= the ARB0 build)
```

**The critical-partition block never appeared.** `update_engine` wrote the entire
Qualcomm boot chain to slot `b` - the same 22 partitions fastboot refused in section 11 -
with the bootloader still in its ordinary `flashing unlock` state. That confirms the
section 12 reading: the refusal is abl's fastboot policy, not a write-protect.

Verification notes:

- **The otacert check is the one to run first.** The zip's `META-INF/com/android/otacert`
  hashed `a4384ba8...` and matched `testkey.x509.pem` inside the built
  `recovery/root/system/etc/security/otacerts.zip` byte-for-byte. That proves the
  self-built recovery will accept the self-built zip *before* committing to a wipe.
- **`has-slot` failed for five of the six step-2 images too** (`dtbo init_boot vbmeta
  vendor_boot recovery`), exactly as it does for firmware. Only `boot` resolved to
  `boot_a`; the rest were sent under bare names. Both `_a` and `_b` report sizes for all
  of them, so they are slotted - the bootloader resolves a bare name to the active slot
  internally. The official instructions rely on this, which is why they work.
- **ARB could not be re-read from the device.** fastboot has no partition-read command
  and there is no root yet. The standing guarantee is `update_engine`'s own per-partition
  SHA-256 check against the payload manifest, which aborts on mismatch; it committed, so
  slot `b` holds the bytes `arbcheck` scored ARB 0. Slot `a` still holds v12.5, also
  ARB 0 - **neither slot can blow the fuse**.
- `ro.build.tags=release-keys` and `ro.build.type=user` are **false** on this build,
  hardcoded for Play Integrity. Same class of lie as the lock state in section 11.

## 13. KernelSU Next + SUSFS

Done 2026-08-30, on top of the ARB0 build from section 12.

### Component choice - and the branch that does not work

The obvious path is the one every tutorial names, and it is wrong now:

| | branch | verdict |
|---|---|---|
| `pershoot/KernelSU-Next` | `next-susfs` | **does not build** - last commit 2025-11-04 |
| `pershoot/KernelSU-Next` | **`dev-susfs`** | **use this** - last commit 2026-08-29 |
| `pershoot/susfs4ksu` | `gki-android14-6.1-lts-dev` | matches our `android14-6.1-lts` @ 6.1.175 |

`next-susfs` is what the Droid Basement tutorial specifies. Against today's SUSFS it
fails to compile:

```
core_hook.c:709: error: assigning to 'int' from incompatible type 'void'
    susfs_cmd_err = susfs_set_cmdline_or_bootconfig((char __user*)arg3);
susfs.h:217: void susfs_set_cmdline_or_bootconfig(void __user **user_info);
```

SUSFS changed that signature; the Nov-2025 driver still calls the old one. SUSFS itself
is maintained daily - the LTS branch landed "Fix offsets (GKI Monthly/LTS)" the same day
this was written - so the driver, not SUSFS, is what goes stale.

### The version trap that actually matters

The two branches number themselves differently, and it decides which Manager APK works:

| | formula | result | official Manager (min 33188) |
|---|---|---|---|
| `next-susfs` | `10000 + n + 200` | 13452 | **rejected** |
| `dev-susfs` | `30000 + n` | **33252** | accepted |

So `next-susfs` also strands you on a 2025-era Manager with no upgrade path, because
every newer Manager refuses the driver. `dev-susfs` is based on **v3.3.0** and works with
the current official APK. The driver accepts two signature hashes - `0x3e6:79e59011...`
(standard) and `0x338:f26471a2...` (spoofed) - both rifsxd's release keys, so official
APKs pass the signature check either way.

**Upstream KernelSU-Next has no SUSFS at all** (no `KSU_SUSFS` in its Kconfig), so a fork
is not optional for this combination.

### Integration

```bash
cd /aosp/kernel/oneplus/sm8650
bash setup.sh next-susfs                  # then: git -C KernelSU-Next checkout dev-susfs
cp -p .../kernel_patches/fs/susfs.c fs/
cp -p .../kernel_patches/include/linux/susfs*.h include/linux/
patch -p1 -i 50_add_susfs_in_gki-android14-6.1.patch
```

The 50_ patch applied to OnePlus's heavily-modified tree with **zero rejects and zero
fuzz** - 23 files, 1473 insertions. Dry-run it first; that was the one real unknown.

It also **bundles the KSU manual hooks** (`ksu_handle_execveat`, `_sucompat`,
`ksu_handle_input_handle_event`), which is why this branch ships no separate
`60_scope-minimized_manual_hooks.patch`. Do not pass `kprobes` to `setup.sh`.

Config fragment `arch/arm64/configs/vendor/ksu.config`, appended to
`TARGET_KERNEL_CONFIG` in `device/oneplus/sm8650-common/BoardConfigCommon.mk`:

```
CONFIG_KSU=y
CONFIG_KSU_SUSFS=y
```

Everything else defaults to `y` in the driver's Kconfig. `dev-susfs` has **no hook-mode
option** - the hooks are unconditional.

`60_modules_no-mmio_tracepoints.patch` was **skipped**. `CONFIG_TRACE_MMIO_ACCESS=y` here
so it is not a no-op, but it is a module perf/symbol tweak, not a SUSFS requirement.

### Do not clone the helper repos inside `/aosp`

They were first cloned to `/aosp/ksu-work`, and kati scanned their `Android.mk`:

```
base_rules.mk:320: error: ksu-work/susfs4ksu/ksu_susfs/jni:
  MODULE.TARGET.EXECUTABLES.ksu_susfs already defined by ksu-work/susfs-upstream/...
```

Anything with an `Android.mk` under the build root becomes part of the build. They live in
`~/Downloads/OP12/ksu-work` now.

### Result

```
-- KernelSU-Next version: 33252      tag: v3.3.0
-- SUSFS_VERSION: v2.3.0
kernel 6.1.175-g3127ab97bcbb-dirty   ARB = 0   (firmware swap unaffected)
```

Built, sideloaded over the running build with **no format** (same test-keys), booted
first try. NikGapps sideloaded in the same recovery session - 28 Google packages,
`/product` still 1.1 GB free.

Verified on-device, kernel-side rather than from the Manager's self-report:

```
$ ksu_susfs show version            -> v2.3.0
$ dmesg | grep susfs
  susfs:[...][susfs_show_version] CMD_SUSFS_SHOW_VERSION -> ret: 0
  susfs:[...][susfs_sdcard_cleanup_fn] /sdcard is decrypted
$ su -c id                          -> uid=0(root) context=u:r:ksu:s0
```

All nine features enabled: `SUS_PATH SUS_MOUNT SUS_KSTAT SPOOF_UNAME ENABLE_LOG
HIDE_KSU_SUSFS_SYMBOLS SPOOF_CMDLINE_OR_BOOTCONFIG OPEN_REDIRECT SUS_MAP`.

### Two things that look wrong and are not

**Hook mode reads "Hybrid", not "manual".** The label is derived purely from config -
with `CONFIG_KSU_SUSFS` set and either `KPROBES` or `HAVE_SYSCALL_TRACEPOINTS` available
(both are, here), `Hybrid` is the only value that branch can emit. This version's
vocabulary is Tracepoint / Kprobes / Hybrid / Inline; "manual" is never printed. The
execve/input hooks are still the inline ones from the 50_ patch.

**The recovery does not verify signatures at all.** In `bootable/recovery/install/install.cpp`
the `verify_package()` call is commented out, so any zip installs. That is why unsigned
NikGapps works - its `META-INF/Nik.RSA` is plain text ("Digital-Message: Author:- ..."),
not a PKCS#7 block. It also means the otacert check in section 12 was correct but not
load-bearing.

### Not verified

Mount hiding. Comparing `/proc/self/mountinfo` as uid 2000 and as root gave 150 mounts and
0 KSU matches **both ways** - there is nothing to hide yet, because the SUSFS module only
runs scripts and mounts nothing. This becomes testable only once a module that overlays
`/system` is installed.


### Play Integrity stack on top (2026-08-30)

Three modules, installed in this order (each depends on the one before):

| module | id | version | role |
|---|---|---|---|
| NeoZygisk (`JingMatrix`) | `zygisksu` | v2.4 (289) | Zygisk provider - KSU-Next ships none |
| AlwaysStrong (`evoker0`) | `tricky_store` | v1.0.3 | TEE-Simulator-RS + PlayIntegrityFork |
| SUSFS module (already present) | `susfs4ksu` | v2.3.0 | root/mount hiding |

**KSU-Next has no Zygisk of its own** - the `zygisk` strings in the tree are the
Manager's *detector*, not a provider. AlwaysStrong needs Zygisk, so NeoZygisk goes first.

**NoMount was deliberately skipped.** It is a mount-hiding VFS framework - the same job
SUSFS already does. On a SUSFS kernel it is redundant, and its authors label it
"research and development, proceed with caution." Nothing gained, brick risk added.

**The NeoZygisk version gate is a warning, not a wall.** Its `customize.sh` has
`MAX_KSU_VERSION=30000`; our driver is 33252 (different numbering scheme, `30000+n`), so
it prints "KernelSU version too large!" and installs anyway - there is no `abort` on that
branch. The gate that *does* abort is `MIN_KSUD_VERSION=11425`; our ksud is 33252, clears
it. No Magisk present (NeoZygisk aborts if it finds one).

Verified live after reboot - the process tree is the proof injection is real:

```
zygisk-ptrace64            (1471)
 zygiskd64                 (1574)
  zygiskd64-tricky_store   (6287)   <- AlwaysStrong companion running inside Zygisk
```

AlwaysStrong [Action] fetched a full profile (engine `asfetch`):

```
keybox.xml   16.9 KB - 3 keys, 30 cert tags, 2 AndroidAttestation chains (real, not stub)
pif.prop     spoofing Pixel 9 Pro XL (komodo), CANARY, security_patch 2026-07-05
             spoofBuild=1 spoofProps=1 spoofVendingFinger=1
```

Result: **MEETS_STRONG_INTEGRITY** confirmed on device.


## 14. Updating the ROM later - keeping ARB0 and hidden root

This is the durable recipe. It exists because **the normal update paths are all traps**
for this device.

### Why you cannot just take an OTA

crDroid ships full-firmware A/B OTAs (section 5): every update payload contains
`xbl_config`, and every official `xbl_config` from v12.6 onward reads **ARB 1**. So:

- **In-app "System update" / OTA** - flashes ARB1 firmware, blows the fuse. **Never use it.**
- **Sideloading the official zip** - same firmware, same fuse (section 12, option 2).
- **Magisk/KSU "install to inactive slot"** off an official zip - same payload, same fuse.

The only safe update is one **you rebuild from source** with the ARB0 firmware swap and
the KSU+SUSFS integration re-applied. There is no shortcut; those live only in your
working tree, not in crDroid's repos.

### What survives an update by itself, and what does not

| survives a no-format sideload | must be re-applied every source update |
|---|---|
| all `/data/adb/modules` (susfs4ksu, NeoZygisk, tricky_store) | ARB0 firmware swap (vendor tree) |
| keybox, pif.prop, module config | KSU-Next + SUSFS (kernel tree) |
| app data, accounts, root grants | `ksu.config` + BoardConfig line (device tree) |

**Root hiding is entirely module-side**, so it survives untouched - *provided the new
kernel still has KSU+SUSFS*. If you rebuild without re-integrating KSU, the modules load
against a non-KSU kernel and root silently breaks. That is what `preflight.sh` guards.

Every one of our source changes is an **uncommitted working-tree edit** across three git
projects (`vendor/oneplus/waffle`, `kernel/oneplus/sm8650`, `device/oneplus/sm8650-common`).
A plain `repo sync` will try to revert them; `repo sync --force-sync` (what the first
build used) *will* revert them. That is why the re-apply scripts exist.

One asymmetry to expect after a sync: the camera patch set is now **six marker edits
plus one whole file we add** (`hardware/oplus/.../OplusHeifWriter.java`, staged in
`/aosp/oplus-cam/newfiles/`). A sync reverts the marker edits but leaves the added file
alone, because it is untracked - so `treepatch.py status` can legitimately report the six
as `clean` and the seventh as `CREATED` at the same time. That mixed state is fine;
`oplus-cam-apply.sh` re-patches the six and skips the one. `status` also flags the case
that actually matters - if a future crDroid starts shipping that path itself, it prints
`UPSTREAM (now tracked by git!)` and `apply` refuses rather than clobbering it.

### The procedure

```bash
cd /aosp
source build/envsetup.sh

# 1. Pull newer crDroid. Do NOT use --force-sync here, so your local edits are
#    not silently discarded; let it report conflicts instead.
repo sync -c --no-clone-bundle --no-tags -j8

# 2. Re-apply the three integrations (all idempotent - safe to re-run any time):
/aosp/ksu-reapply.sh          # KSU-Next dev-susfs + SUSFS + ksu.config
/aosp/arb0-reapply.sh         # v12.5 ARB0 xbl/xbl_config/xbl_ramdump/abl + SHA1 pins
/aosp/oplus-cam-apply.sh      # camera tree patches (6 edits + 1 added file)

# 3. Build.
brunch waffle

# 4. GATE - refuses to pass on ARB1 or a KSU-less kernel. Run it every time.
/aosp/preflight.sh
```

Only if preflight prints **PREFLIGHT PASS** do you flash.

### Flashing the update (no wipe)

Same test-keys as the running build, so this is a dirty flash - **no format**, data and
all modules survive:

```bash
# extract the 6 boot images from the new build (or reuse device/ scripts):
adb reboot bootloader
for p in boot dtbo init_boot vbmeta vendor_boot recovery; do
  fastboot flash $p /aosp/out/target/product/waffle/$p.img
done
fastboot reboot recovery          # on-device: Apply update > Apply from ADB
adb sideload /aosp/out/target/product/waffle/crDroidAndroid-16.0-*-waffle-v12.11.zip
# reboot to system. Modules reload automatically; root hiding intact.
```

update_engine writes the inactive slot and switches, so the previous build stays on the
other slot as a fallback (section 12). Both slots remain ARB0.

### Two judgement calls that recur each update

**When crDroid bumps the bootloader group.** `arb0-reapply.sh` always forces our v12.5
ARB0 `xbl`/`xbl_config`/`xbl_ramdump`/`abl` in, regardless of what crDroid shipped. If a
future crDroid moves to a newer OOS firmware generation, a v12.5 `xbl_config` against a
much newer `tz`/`hyp`/`devcfg` could fail to boot (ARB.md section 5 caveat). It is all
ARB0 and *likely* fine, but keep MSM/EDL ready on any update that changes the non-swapped
firmware. Check with: `python3 arbcheck.py <new official zip>` - if the official build is
still ARB1 only in `xbl_config`, the swap set is unchanged and low-risk.

**When the SUSFS API drifts again.** `ksu-reapply.sh` pulls `dev-susfs` HEAD and the
current `susfs4ksu` LTS branch. These are matched *today*; if a rebuild ever fails with a
`susfs_*` signature error (section 13), the two halves are briefly out of sync - wait a
day or pin `susfs4ksu` to the commit that matches the driver, exactly the failure that
killed the first attempt.

### Updating the modules themselves

Independent of ROM updates, and safe in-place:

- **NeoZygisk / SUSFS module**: newer zip -> Manager -> install -> reboot.
- **AlwaysStrong**: newer zip the same way; keybox/pif carry over in `/data/adb/tricky_store`.
- **The Manager APK** is the spoofed build (random package name `vctsrt.cntgtj.uqfwgg`),
  so it will **not** self-update and a new release has a *different* random name. Update it
  by installing the new spoofed APK, then uninstalling the old package. Both APKs are kept
  in `~/Downloads/OP12/ksu-userspace/`.

### Files this procedure relies on

```
/aosp/ksu-reapply.sh      re-integrate KSU-Next dev-susfs + SUSFS (idempotent)
/aosp/arb0-reapply.sh     re-apply ARB0 firmware swap + SHA1 pins (idempotent)
/aosp/oplus-cam-apply.sh  re-apply the camera tree patches (idempotent)
/aosp/oplus-cam/newfiles/ whole files the camera port ADDS to the tree
/aosp/oplus-cam/verify-livephoto.sh  post-flash camera/Live Photo test (PASS/FAIL)
/aosp/oplus-cam/vtable-dump.py       dump a C++ vtable from a .so (APS2 relocs)
/aosp/preflight.sh        flash gate: ARB0 + KSU present (run every time)
/aosp/arb0-stash/         the 4 known-good ARB0 blobs (donor for arb0-reapply)
~/Downloads/OP12/ksu-work/susfs4ksu   susfs4ksu gki-android14-6.1-lts-dev clone
~/Downloads/OP12/ksu-userspace/       Manager APKs + module zips
```


## 15. Undoing the changes to this machine

Everything below is reversible. Listed most-transient first, so you can stop wherever you
like.

### The swapfile

```bash
sudo swapoff /aosp/swapfile          # takes a moment: pages must be read back into RAM
sudo rm -f /aosp/swapfile            # reclaims 32 GiB
sudo sed -i '\|/aosp/swapfile|d' /etc/fstab    # only if you added the fstab line
swapon --show                        # confirm: only /dev/zram0 should remain
```

Run `swapoff` while memory is quiet - if 20 GiB is currently paged out it has to come back
into RAM first, and doing that mid-build will OOM the machine. If a reboot has happened
since, the swap is already inactive and only the `rm` is needed.

### The systemd-oomd drop-in (only if you applied it)

```bash
sudo rm -f /etc/systemd/system/user@1000.service.d/no-oomd.conf
sudo rmdir --ignore-fail-on-non-empty /etc/systemd/system/user@1000.service.d
sudo systemctl daemon-reload
systemctl show user@1000.service -p ManagedOOMMemoryPressure   # back to "auto"
```

The setting only takes effect for the user session on next login, so removing it mid-session
is harmless either way.

### The transient build service

```bash
systemctl --user stop aospbuild 2>/dev/null      # if still running
systemctl --user reset-failed aospbuild
```

Transient units vanish on their own at reboot; `reset-failed` just clears the failed record.

### The update/KSU tooling (build-tree only)

```bash
rm -f /aosp/ksu-reapply.sh /aosp/arb0-reapply.sh /aosp/preflight.sh
rm -rf /aosp/arb0-stash
```

These are plain scripts and a 2.4 MB blob stash; they touch nothing system-wide. The
KSU/SUSFS integration itself reverts with the kernel tree below (it is uncommitted
working-tree state); `~/Downloads/OP12/ksu-work` and `ksu-userspace` are just files in
your home directory.

### The build tree

`/aosp` is a btrfs subvolume, not a plain directory, so `rm -rf` is the slow way. Delete
the subvolume instead:

```bash
sudo btrfs subvolume delete /aosp     # reclaims the whole ~170 GiB at once
```

If that refuses because nested subvolumes exist (some AOSP trees create them), list and
delete children first:

```bash
sudo btrfs subvolume list -o /aosp
```

To keep the tree but reclaim only build output, `rm -rf /aosp/out` (~150 GiB once built),
or `/aosp/.ccache` (up to 50 GiB).

### The host packages

```bash
sudo dnf remove gperf libxcrypt-compat ncurses-compat-libs
```

Consider leaving these - they are small and commonly needed by other builds.
`zlib-ng-compat-devel` was already present and should not be removed.

### What is not a machine change

`repo` was installed by you beforehand; `git config --global user.name/user.email` were
already set. The `/home/w/Downloads/OP12` tooling (`arbcheck.py`, `arbswap.py`, `ARB.md`,
`ROM.md`) is just files in your home directory and touches nothing system-wide.

## 16. OPlus stock camera - and the things that bite on this device

Attempted 2026-09-03. The camera work itself now lives in
**[CAMERA.md](CAMERA.md)** - build, blob split, module internals, resume point.
What follows is the *device-level* knowledge that came out of it, which applies
to **any** future module and includes the only way to rescue this phone.

### What was built

| piece | where |
|---|---|
| in-tree native patches (6 marker-wrapped edits + 1 added file) | `/aosp/oplus-cam/treepatch.py`, `oplus-cam-apply.sh` / `oplus-cam-revert.sh` |
| KSU module: app + 73 blobs from OOS 16 | `/aosp/oplus-cam/`, built by `build-module.py` |
| blob source | `OTA/CPH2581_16.0.8.300(EX01).zip` (OOS 11.F.54, CPH2581 **EEA**) |

The three native patches come from `dodge-camera-port` (OnePlus 13): QTI vendor
YUV formats in `AHardwareBuffer_formatIsYuv` (without them photos are green and
distorted), and a compile-time `ALLOW_NONINCREASING_TIMESTAMPS` for
`Camera3Stream` (the OPlus HAL reports non-monotonic frame timestamps and AOSP
errors every such buffer). `device.mk` sets the two soong configs plus
`ro.oplus.cam.treepatch=1`, which the module's `customize.sh` gates on.

Of the port's 203 `proprietary-files.txt` entries, **128 are already in the
crDroid vendor tree** - which is `CPH2573_16.0.9.400`, one minor version
*newer* than the dump - so the module only supplies the 73 that are missing.
Both prebuilt APKs are re-signed with `build/make/target/product/security/platform.pk8`,
because the stock APKs carry OnePlus's platform cert, which is not the platform
cert here.

### Finding 1: overlayfs cannot touch `/data` on this phone

`ro.crypto.type=file` - fscrypt. Kernel 6.1 overlayfs rejects an encrypted
filesystem as **any** layer, upper or lower:

```
overlayfs: filesystem on '/data/adb/modules/opluscam_waffle/system_ext' not supported
```

The consequence is bigger than this module: **`ksud`'s module file-mounting is a
silent no-op on this device.** Any KSU module that ships a `system/` tree will
report "Module installed successfully!", mount nothing, and give no error. The
boot log shows sepolicy -> features -> `post-fs-data.sh` -> `system.prop` ->
Services, with no mount stage at all, and `/proc/mounts` has zero module mounts.

This was never noticed before because none of the five installed modules ship
files - section 13 already recorded "the SUSFS module only runs scripts and
mounts nothing".

Workaround that does work: stage the payload on **tmpfs** (unencrypted, which
overlayfs accepts), relabel it to the target contexts, and overlay from there.
Verified live - the APK appeared as `u:object_r:system_file:s0` and the JNI libs
as `u:object_r:system_lib_file:s0`.

### Finding 2: never overlay a partition root at post-fs-data

Doing exactly that (`/system`, `/vendor`, `/system_ext`, `/odm`) **hard-hangs the
boot** - black screen, no boot animation, no adb, no fastboot. By post-fs-data
init has already mounted the APEXes and built the linker namespaces against
those paths; shadowing them invalidates all of it.

Overlaying only **leaf directories** (`priv-app`, `framework`, `lib64`,
`etc/permissions`, `vendor/lib64`, `vendor/etc`, `odm/lib64`, `odm/etc/camera`)
achieves the same result with no boot involvement.

Note that a mount sequence which works fine when run live on a booted system
proves nothing about running it *during* boot.

### Finding 3: recovery cannot mount `/data`, so safe mode is the only rescue

`/data` is metadata-encrypted (dm-default-key) and recovery never sets the
mapping up. Confirmed by sideloading a diagnostic zip:

```
mountpoint cmd: yes
proc/mounts: /data NOT mounted
entries under /data: 0
/dev/block/mapper/userdata      -> failed
/dev/block/by-name/userdata     -> failed
/dev/block/bootdevice/by-name/userdata -> failed
```

**No sideload zip can ever repair `/data` on this phone.** A bad module is not
fixable from recovery.

#### KernelSU safe mode needs PRESSES, not a hold

This is the rescue, and every guide describes it wrongly. From
`KernelSU-Next/kernel/runtime/ksud_integration.c`:

```c
if (*type == EV_KEY && *code == KEY_VOLUMEDOWN) {
    if (val) {                       /* key-DOWN edge only */
        volumedown_pressed_count += 1;
    }
}
static bool is_volumedown_enough(unsigned int count) { return count >= 3; }
```

It counts key-**down edges** and needs **>= 3**. Holding the button generates one
event and can never trigger it. The check runs at post-fs-data, ~4 s after
kernel start.

**Procedure:** force-restart (Power + Vol Up ~15 s), then from the moment the
orange state warning appears, **tap Volume Down ~8-10 times, releasing fully
between taps**, over about 10 seconds. `ksud` then skips every module and the
system boots with root intact. Afterwards, safe mode leaves **all** modules
flagged `disable` - delete `/data/adb/modules/*/disable` and reboot to restore
them.

### Key combos on waffle, for the record

| | |
|---|---|
| force restart | Power + Vol Up (~15 s) - restarts, there is no true power-off |
| fastboot | Vol Up + Vol Down + **Power**, held *through* the force restart |
| recovery | Vol Down + Power from off, or `fastboot reboot recovery` |
| **EDL** (avoid) | Vol Up + Vol Down + plug USB, **no Power** - `05c6:900e` |

Landing in EDL is harmless by itself. **Never run an MSM/EDL flash tool on this
device** - stock OOS packages carry ARB 1 firmware and would blow the fuse this
whole project exists to avoid.

Because Power + Vol Up restarts rather than powers off, the volume keys must be
held *continuously through* the reset - the bootloader samples them as it powers
back up. Releasing and re-pressing is too slow.

### Current state

Build **`1788591024`** on slot **`_b`** (flashed 2026-09-05), root `u:r:ksu:s0`,
SUSFS v2.3.0, Zygisk injecting, all five original modules plus
`opluscam_waffle`. ARB 0 on both slots; GApps reinstalled. In-tree patches
applied (6 marker edits + 1 added file).

**The OPlus stock camera works**: photo, video and portrait all capture on all
three rear lenses, preview is live and sustained, the HAL survives with zero
tombstones, and the app survives every mode switch. Stills are Ultra HDR JPEGs
with full EXIF; video is HEVC 1080p30 with audio. Portrait saves - a mode the
reference port itself lists as broken. Verified from EXIF: ultrawide (camera 3,
14 mm f/2.2), main (camera 2, 23 mm f/1.6), periscope (camera 4, 70 mm f/2.6 at
its native 3456x4608). The zoom bar's `2x` and `6x` chips are digital crops,
not lenses.

**Live Photo works too (fixed 2026-09-05, session 8).** It used to leak
gralloc buffers until lmkd killed the app; the cause was our
`ImageReader.nativeGetConsumer()` returning the wrong object to an OOS blob
that expects an `IGraphicBufferConsumer*`. Not an ABI mismatch - OxygenOS's
and crDroid's `libgui` vtables agree at every slot the blob uses, and the blob
ships stock. CAMERA.md section 15.

Three device-level root causes were worth recording here rather than in
CAMERA.md, because they generalise to any port on this phone:

* **crDroid's `extract-files.py` does not extract `odm/lib64/libui.so`.** The
  ROM's `/vendor/lib64/libui.so` is AOSP 16's Gralloc5 build, which calls
  through a NULL pointer inside `Gralloc5Mapper` in the vendor camera
  processes; OxygenOS ships a Gralloc4 copy in `/odm/lib64` precisely so its
  odm blobs bind to it. The module installs it under a private name
  (`oemui.so`) and repoints the eight camera blobs - installing it as
  `libui.so` fixes the camera but **hangs the boot**, because the display stack
  picks it up too.

* **SELinux binder rules are directional, and a HAL callback needs the reverse
  one.** The module granted `platform_app -> hal_camera_default` but not
  `hal_camera_default -> platform_app`. The QTI offline-camera HAL calls
  `AIBinder_associateClass` on the callback the app hands it, which is an
  `INTERFACE_TRANSACTION` *back into the app*. Denied, the descriptor came back
  empty, the callback was rejected as null, and `libAlgoProcess.so` then ran
  `configureOfflineStreams()` on a NULL session - SIGSEGV, no file saved. The
  signature to look for is `associateClass: ... but descriptor is actually ''`
  with a reverse-direction avc denial just before it.
* **`crash_dump` could not write tombstones at all.** It builds its unwinder from
  a memfd copy of `/proc/<pid>/maps`, and on this ROM that memfd is labelled
  plain `tmpfs`, which `crash_dump` may not write - so *every* app-process
  native crash produced a bare `F libc: Fatal signal 11` and nothing else.
  `allow crash_dump tmpfs file { read write open getattr map }` fixes it. Worth
  adding on any bring-up before chasing a native crash; it cost a session here.

The third session-4 fix was app-side: `t7.u3` (`TypeFaceUtil`) reads
`OplusBaseConfiguration.mOplusExtraConfiguration` off a null `typeCasting()`
result and throws an NPE its own try/catch does not cover, killing the app on
any PORTRAIT switch. `/aosp/oplus-cam/patch-apk-typeface.py` stubs the method to
`Typeface.DEFAULT` using AOSP's own `smali`/`baksmali` prebuilts - no apktool.

CAMERA.md section 1 has all of it, with the traces.

Two earlier root causes generalise beyond the camera and are recorded below as
findings 4 and 5. A third was a missing soong config - `camera/package_name` -
without which
`Camera3Device::configureStreamsLocked` never told the HAL which app was
calling, so the HAL applied a reduced third-party stream table and rejected the
telephoto stream. CAMERA.md section 9 has the full chain.

`post-fs-data.sh` overlays leaf directories only and carries a **sticky**
self-disarming guard: it drops `/data/adb/opluscam_boot_pending` before
mounting, `service.sh` clears it at `sys.boot_completed`, and a boot that never
completes causes the next boot to create `/data/adb/opluscam_no_mount` and skip
mounting permanently. Sticky on purpose - a one-boot skip would alternate hang
/ skip / hang. Re-arm with `rm /data/adb/opluscam_no_mount`.

### Finding 4: re-signing an app with the platform key moves its SELinux domain

`build-module.py` re-signs the prebuilt APKs with the ROM's platform key so
they get their signature-protected permissions. That also makes
`seapp_contexts` match `seinfo=platform`, so the app runs as **`platform_app`**
- *not* `priv_app`, despite living in `/system_ext/priv-app`:

```
u:r:platform_app:s0:c512,c768  com.oplus.camera
u:r:system_app:s0              com.oplus.appplatform
```

The module's entire `sepolicy.rule` had been written for `priv_app` and was
therefore inert. Two denials were the only symptom, and they were not obviously
fatal:

```
avc: denied { read } for name="libOplusSecurity.so"
     scontext=u:r:platform_app:s0 tcontext=u:object_r:vendor_file:s0
```

Applies to **any** module that ships a platform-signed app: write the policy
for the domain the process actually lands in, and check with `ps -AZ` rather
than assuming.

### Finding 5: OPlus camera configs are AES-encrypted

`/odm/etc/camera/config/oplus_camera_config` and friends are AES-128-ECB blobs
(magic `01 01`, 4-byte header and footer). Grepping them for a setting always
finds nothing, which makes it easy to conclude a key is absent when it is
merely encrypted. `/aosp/oplus-cam/patch-oplus-config.py` decrypts, edits and
rewrites them as plaintext, which the SDK accepts.

A silent decrypt failure is expensive: `ApsUtils.initConfigData()` catches the
resulting JSON exception and leaves **every** key at its hardcoded default, so
one SELinux denial silently reconfigured the whole camera stack.

### ARB re-verified from the device

Section 12 recorded ARB as unverifiable on-device ("fastboot has no
partition-read command and there is no root yet"). With root it is:

```
dd if=/dev/block/by-name/{xbl,xbl_config,abl}_{a,b} -> arbcheck.py
  all six images: ARB = 0
  xbl / xbl_config / abl: byte-identical across both slots
```

Not inference from the payload manifest - the actual flash contents.

## 17. Status log

Milestones only. Every lesson below is written up properly in the section
named; this table exists to date things, not to explain them.

| When | Milestone | Detail in |
|---|---|---|
| 2026-08-29 | `/aosp` btrfs subvolume (nodatacow), `repo init`, sync started | §2-3 |
| 2026-08-30 | `repo sync` done - 117 GB; `breakfast waffle` resolved 11 projects in one pass | §3 |
| 2026-08-30 | Two builds killed by OOM (`soong_build` peaked **27.5 GiB** RSS on 31 GiB); fixed with a **32 GiB swapfile** - zram cannot cover that peak | §4 |
| 2026-08-30 | **First build succeeded** (5h45m). Identical fingerprint and 18/18 bit-identical firmware vs official - baseline established, reads ARB 1 | §5 |
| 2026-08-30 | ARB0 firmware swap; rebuild failed on per-blob SHA1 pins, recomputed all 25 | ARB.md §8 |
| 2026-08-30 | **ARB0 build succeeded**; fastboot flash refused (critical partitions), sideload writes firmware via `update_engine` with no `unlock_critical` | §12 |
| 2026-08-30 | **Booted the ARB0 build.** Both slots ARB 0 | §12 |
| 2026-08-30 | KSU+SUSFS integrated (`dev-susfs`, not the tutorial's dead `next-susfs`); NikGapps; **MEETS_STRONG_INTEGRITY** | §13 |
| 2026-08-30 | Update tooling written: `ksu-reapply.sh`, `arb0-reapply.sh`, `preflight.sh` | §14 |
| 2026-09-03 | ARB re-verified on-device with root (`dd` both slots, byte-identical) | §12 |
| 2026-09-03 | Camera module built (73 blobs, OOS 16.0.8 EEA). Mounted nothing - **fscrypt makes overlayfs reject `/data`** | §16 |
| 2026-09-03 | Overlaying partition **roots** hard-hung the boot; rescued via **KernelSU safe mode**. Rewrote to leaf directories + self-disarm guard | §16 |
| 2026-09-03 | Camera app launches; session configures with zero errors after the `camera/package_name` soong fix | CAMERA.md §9 |
| 2026-09-04 | Preview fixed - the ROM's `libui.so` is Gralloc5, OxygenOS's is Gralloc4; shipped as `oemui.so` with `DT_NEEDED` repointed in 8 blobs | CAMERA.md §1 |
| 2026-09-04 | **First photo saved.** Then video, then PORTRAIT; all three rear lenses verified | CAMERA.md §1 |
| 2026-09-04 | `crash_dump` could not write any app tombstone (SELinux on its memfd) - fixing that unblocked every later diagnosis | CAMERA.md §1 |
| 2026-09-05 | `OplusHeifWriter` ported; Live Photo still leaks (believed an ABI mismatch) | CAMERA.md §14 |
| 2026-09-05 | **Live Photo leak fixed**: `nativeGetConsumer()` returns `getIGraphicBufferConsumer().get()`; proven with Frida on the old build, then JNI-only rebuild (15 min) + flash; **camera port complete** | CAMERA.md §15 |

Retractions worth remembering, because each cost a session:

* "Preview works" (2026-09-03) - based on one screenshot that caught a ~1 s
  burst of a crash-looping HAL. **Diff two screenshots a second apart.**
* "The periscope never engages" (2026-09-04) - stale tap coordinates; the
  mode/zoom strip scrolls. *Partly* un-retracted: the app really does drop the
  f/2.6 tele for a crop of the f/1.6 main below some lux threshold.
* "The foreground kill is a memory trim" (2026-09-04) - it was a real dmabuf
  leak; `MemAvailable` measured *after* the kill always looks healthy.
* "`ctx->getBufferConsumer()` is correct, do not second-guess it"
  (2026-09-04) - it is not correct. CAMERA.md §14.
* "It is an ABI mismatch against a hardcoded vtable slot" (2026-09-05 a.m.) -
  it was not; both libguis agree. The vtable table was anchored one slot off
  on a crash frame. **Anchor on the RTTI qword, and dump both libraries.**
  CAMERA.md §15.
