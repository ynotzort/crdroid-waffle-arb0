> Copied verbatim from the working notes on 2026-09-05. `arbcheck.py` and
> `arbswap.py` referred to here live in `tools/`.

# OnePlus 12 (waffle / SM8650) — Anti-Rollback (ARB) analysis

**TL;DR — the ARB level of a OnePlus 12 firmware set lives in exactly one file:
`xbl_config.img`, in a single 32-bit field of its signed OEM metadata block.
Nothing else in the firmware chain carries it.**

Analysis date: 2026-08-29. Device: OnePlus 12, codename `waffle`, model `CPH2573IN` /
`OP595DL1`, SoC Snapdragon 8 Gen 3 (SM8650).

---

## 1. Summary of the finding

Across **17 signed Qualcomm firmware images** plus the 483 signed sub-images inside
`modem`, exactly **one byte** differs in the signed metadata between a known-ARB0
crDroid build and the first ARB1 build:

```
xbl_config.img @ file offset 0x38048 (229448)

  crDroid 16.0 v12.5  (2026-01-01):  00 00 00 00     anti_rollback_version = 0
  crDroid 16.0 v12.11 (2026-08-09):  01 00 00 00     anti_rollback_version = 1
```

Every other image — `xbl`, `abl`, `tz`, `hyp`, `devcfg`, `aop`, `aop_config`, `uefi`,
`uefisecapp`, `keymaster`, `cpucp`, `cpucp_dtb`, `shrm`, `qupfw`, `imagefv`,
`oplus_sec`, `xbl_ramdump`, `modem` — reads `anti_rollback_version = 0` in **both**
builds. The ARB bump is carried solely by `xbl_config`.

### Who enforces it

XBL. It contains the Qualcomm anti-rollback manager:

```
$ strings xbl.img | grep -i rollback
/sources/services/antirollback/src/AntiRollbackMgr.cpp
ARBConfig:MAX_NUM_ARB_ADDRESS
ARBConfig:arbFuseMapQti
ARBConfig:arbFuseMapOem
```

XBL authenticates `xbl_config` early in boot, reads its `anti_rollback_version`, and
blows the corresponding OEM anti-rollback QFPROM fuse to match. QFPROM fuses are
one-time-programmable: once the OEM ARB fuse reads 1, XBL will refuse to authenticate
any image whose ARB version is 0, so **every ARB0 firmware set becomes unbootable,
permanently**. This also means an ARB0 MSM/EDL unbrick package will no longer work.

---

## 2. Where the field lives — image format

OP12 firmware images are ELF containers using **Qualcomm Secure Boot 3.0 / MBN header
version 7**. Each ELF has one program header whose QC segment type is `2`
(*hash table segment*), identified by `(p_flags >> 24) & 0xff == 2`. That segment holds
the entire signed metadata block.

### Hash-segment layout

```
+---------------------------------------------------+
| MBN v7 header                          40 bytes   |
| common metadata               common_metadata_size|
| QTI metadata                     qti_metadata_size|
| OEM metadata                     oem_metadata_size|   <-- ARB is in here
| hash table                        hash_table_size |
| QTI signature                   qti_signature_size|
| QTI cert chain                 qti_cert_chain_size|
| OEM signature                   oem_signature_size|
| OEM cert chain                 oem_cert_chain_size|
+---------------------------------------------------+
```

### MBN v7 header (40 bytes, all little-endian u32)

| Offset | Field | Typical value |
|--------|-------|---------------|
| `0x00` | image_id | `0` |
| `0x04` | header_version | `7` |
| `0x08` | common_metadata_size | `24` |
| `0x0c` | qti_metadata_size | `0` |
| `0x10` | oem_metadata_size | `224` |
| `0x14` | hash_table_size | `48 * n_segments` (SHA-384) |
| `0x18` | qti_signature_size | `0` |
| `0x1c` | qti_cert_chain_size | `0` |
| `0x20` | oem_signature_size | `104` (DER ECDSA P-384) |
| `0x24` | oem_cert_chain_size | `3360` |

The 40-byte header size and this field order are **derived**, not read from a spec, but
they are confirmed arithmetically. For `abl.img` the hash segment `p_filesz` is
`0xf38` = 3896, and:

```
40 + 24 + 0 + 224 + 144 + 0 + 0 + 104 + 3360 = 3896   exact
```

The same arithmetic holds for every other image, and `hash_table_size / n_program_headers`
is always exactly 48 bytes (SHA-384), which independently confirms the field order.

### Common metadata (24 bytes)

| Offset | Field | Observed |
|--------|-------|----------|
| `+0x00` | metadata major version | `0` |
| `+0x04` | metadata minor version | `0` |
| `+0x08` | **sw_id** (u64) | image identifier, see table below |
| `+0x10` | secboot version | `3` |
| `+0x14` | reserved | `0` |

The `sw_id` values match Qualcomm's documented software IDs (TZ = `0x07`,
APPSBL/`abl` = `0x1c`, `devcfg` = `0x05`, `hyp` = `0x15`, `aop` = `0x21`), which
validates that the header offset and size are being parsed correctly.

### OEM metadata (224 bytes) — the ARB field

| Offset | Word idx | Field | ARB0 | ARB1 |
|--------|----------|-------|------|------|
| `+0x00` | 0 | metadata version | `3` | `3` |
| `+0x08` | 2 | **anti_rollback_version** | **`0`** | **`1`** |
| `+0x10` | 4 | hw/soc id | `0xa00c` | `0xa00c` |
| `+0x88` | 34 | (oem id / model) | `0x51` | `0x51` |
| `+0xdc` | 55 | (oem build tag) | `0x155a56` | `0x155a56` |

All other words in the 224-byte block are zero.

The name `anti_rollback_version` for word 2 is an attribution, not something read off a
label in the binary. The evidence for it:

1. It is `0` in every build the community classifies as ARB0 and `1` in the build
   classified as ARB1 — matching the exact 0→1 transition the user reported.
2. It is the **only** metadata change anywhere in the entire signed firmware chain
   between those builds.
3. It only appears in `xbl_config`, and `xbl` — the image that reads and validates
   `xbl_config` — is the one containing `AntiRollbackMgr.cpp` and `arbFuseMapOem`.

---

## 3. Observed `sw_id` values (OP12 / SM8650)

| sw_id | Image |
|-------|-------|
| `0x05` | devcfg |
| `0x06` | (modem sub-image) |
| `0x07` | tz |
| `0x08` | (modem sub-image) |
| `0x09` | uefi |
| `0x15` | hyp |
| `0x1c` | abl |
| `0x1d` | (modem sub-image) |
| `0x20` | shrm |
| `0x21` | aop |
| `0x24` | qupfw |
| `0x25` | **xbl_config** |
| `0x27` | imagefv |
| `0x31` | cpucp |
| `0x35` | xbl sub-image 1 |
| `0x36` | xbl sub-image 2 |
| `0x3d` | aop_config |
| `0x3e` | (modem sub-image) |
| `0x40` | (modem sub-image, ~450 instances) |
| `0x42` | xbl_ramdump |
| `0x64` | cpucp_dtb |
| `0x…0000000c` | signed TA / app images (featenabler, keymaster, oplus_sec, uefisecapp) — low 32 bits `0x0c` = "app", high 32 bits = per-app id |

---

## 4. Survey of local ROM builds

| ROM build | `xbl_config` ARB | Verdict |
|-----------|------------------|---------|
| crDroid 15.0 v11.7 — 2025-08-13 | 0 | safe |
| Axion 2.1 — 2025-10-28 | 0 | safe |
| Xperience 20.0.0 — 2025-10-29 | 0 | safe |
| crDroid 16.0 v12.3 — 2025-11-15 | 0 | safe |
| crDroid 16.0 v12.5 — 2026-01-01 | 0 | safe |
| **crDroid 16.0 v12.11 — 2026-08-09** | **1** | **blows the fuse** |

The bump landed somewhere between v12.5 (2026-01-01) and v12.11 (2026-08-09).

---

## 5. Why this matters when flashing crDroid

crDroid for `waffle` ships as a **full-firmware A/B OTA**. `payload.bin` in
`crDroidAndroid-16.0-20260809-waffle-v12.11.zip` contains **40 partitions**, including
the complete Qualcomm boot chain:

```
abl  aop  aop_config  bluetooth  boot  cpucp  cpucp_dtb  devcfg  dsp  dtbo
engineering_cdt  featenabler  hyp  imagefv  init_boot  keymaster  modem  odm
oplus_sec  oplusstanvbk  product  qupfw  recovery  shrm  splash  system
system_dlkm  system_ext  tz  uefi  uefisecapp  vbmeta  vbmeta_system
vbmeta_vendor  vendor  vendor_boot  vendor_dlkm  xbl  xbl_config  xbl_ramdump
```

Sideloading that zip, or letting `update_engine` apply it, flashes `xbl_config` and the
fuse blows on the next boot. To stay on ARB0 you must dump `payload.bin` and flash
partition-by-partition, substituting an **ARB0-signed** `xbl_config`.

### The byte cannot be patched

The ARB field sits inside the region covered by the OEM ECDSA P-384 signature and the
OEM cert chain. Flipping `01` back to `00` invalidates the signature and XBL will refuse
the image (best case a boot failure into EDL). The only workable route is to substitute
a genuinely OnePlus-signed `xbl_config` from an older, ARB0 build.

### Practical substitution set

`undoarb.bat` (present in this directory) replaces `abl`, `xbl`, `xbl_config`,
`xbl_ramdump` — and this analysis supports that choice. Only `xbl_config` strictly
carries the flag, but `xbl` ↔ `xbl_config` are a matched pair (DDR training tables,
clock/PMIC config), so mixing versions across that boundary risks a hard EDL brick.
Swapping the group as a coherent set from one older build is the conservative call.
The newest ARB0 set available locally is **crDroid v12.5 (2026-01-01)**.

### Caveats

- This analysis verifies the **ARB metadata field values** statically. It does **not**
  prove that v12.5's XBL boots cleanly against v12.11's `tz` / `hyp` / `devcfg` / `aop`.
  That is likely fine (all ARB0, same OOS generation) but is not established here.
- Have an MSM / EDL unbrick package ready before flashing a mixed bootloader set.
- `vbmeta` rollback indexes are a **separate** mechanism (Android Verified Boot,
  stored in RPMB, not a QFPROM fuse) and are unrelated to this ARB fuse.

---

## 6. Checking a ROM: `arbcheck.py`

`arbcheck.py` (this directory) reports the ARB version of an OTA zip, a bare
`payload.bin`, or a raw firmware `.img`. Stdlib only — no venv, no protobuf, no
`payload_dumper.py`. It seeks directly into the stored `payload.bin` inside the zip
and decompresses only the partitions it needs, so a 3 GB ROM is checked in ~0.25 s.

```bash
python3 arbcheck.py crDroidAndroid-16.0-20260809-waffle-v12.11.zip
python3 arbcheck.py -p xbl_config *.zip          # fast path, just the carrier
python3 arbcheck.py --json rom.zip               # machine-readable
python3 arbcheck.py -a rom.zip                   # also scan modem/dsp (slow)
python3 arbcheck.py out/xbl_config.img           # raw image
```

Exit codes: `0` = ARB 0 (safe), `1` = ARB >= 1 (will blow the fuse),
`2` = could not determine. Suitable for a pre-flash guard:

```bash
python3 arbcheck.py rom.zip || { echo "REFUSING TO FLASH"; exit 1; }
```

### Design note: undetermined is not safe

The parser validates `header_version == 7` and checks that the nine MBN size fields sum
to the hash segment's `p_filesz`. If either check fails, the image is reported
`UNKNOWN` and the exit code is `2` — it is never reported as ARB 0. If `xbl_config` is
present in a payload but cannot be parsed, the whole verdict becomes `UNKNOWN`. A
checker that silently answered "0" on an unrecognised format would claim "safe" while
having no idea, and the cost of that particular error is an irreversible fuse.

Partition selection is by *denylist* (`NON_FIRMWARE`) plus a 16 MB size cap rather than
an allowlist of known firmware names, so a firmware partition that OnePlus adds later is
inspected automatically instead of being silently skipped.

### Validation

The hand-rolled protobuf manifest reader and the extent/decompression logic were checked
by reconstructing all 18 firmware partitions of the v12.11 payload and comparing SHA-256
against the output of the reference `payload_dumper.py`: **18/18 byte-identical**.
Results across all six local ROM builds match the table in section 4.

## 7. Building an ARB0 image set: `arbswap.py`

`arbswap.py` takes an ARB1 target ROM and an older ARB0 donor ROM, extracts every
partition from the target *except* the ARB-carrying bootloader group which comes from
the donor, verifies the result on disk, and emits a fastboot command list.

```bash
python3 arbswap.py -n TARGET.zip DONOR.zip          # survey + validate, write nothing
python3 arbswap.py -o arb0_out TARGET.zip DONOR.zip # full set (8.06 GiB, ~2 min)
python3 arbswap.py --firmware-only ...              # Qualcomm chain only (~15 MB)
python3 arbswap.py --superhybrid ...                # SuperHybrid folder layout
python3 arbswap.py --deep ...                       # also survey modem/dsp (slow)
```

Exit codes: `0` = ARB0 set produced, `1` = refused, `2` = error.

### What it does not do

- **It does not rebuild a flashable OTA zip.** `payload.bin` carries a per-operation
  SHA-256 plus a signed metadata blob, and recovery checks the zip against the ROM's
  release key. Substituting a partition invalidates all three and the signing key is
  not available. Output is loose `.img` files plus a script.
- **It does not run `fastboot`.** It writes `flash.sh` for review. A wrong command here
  bricks the phone, and the generated commands cannot be tested without the device.

### Refusal conditions

It refuses, and writes no flash script, when:

- a partition in the target carries ARB >= 1 but is **not** in the substitution set —
  i.e. the flag moved and the default set is stale;
- the donor is not actually ARB0 for every image being taken from it;
- the donor and target are different devices (`pre-device` mismatch);
- after writing, any image **on disk** still reads ARB >= 1;
- `xbl_config` was written but its ARB cannot be read back.

The final check re-reads the bytes from disk and re-derives ARB from them, rather than
trusting what the extraction stage believed it wrote.

### Slot policy

The Qualcomm firmware chain is flashed to **both slots**. An ARB1 `xbl_config` left on
the inactive slot still blows the fuse when that slot is validated, and a half-swapped
chain on the inactive slot is incoherent. The generated script asks the device
`fastboot getvar has-slot:<partition>` instead of assuming a partition is slotted.
Boot images and logical partitions go to the active slot, which is what an OTA writes.
`--active-slot-only` disables both-slot firmware flashing and is not recommended.

Otherwise the emitted commands reproduce what `update_engine` would have written: no
`erase`, no `format`, no added `--disable-verity` flags.

### `--superhybrid` layout is inferred, not verified

`--superhybrid` puts the Qualcomm firmware chain in `FASTBOOTD_FILES_HERE` and the boot
images plus logical partitions in `FASTBOOT_FILES_HERE`. That split is **derived from a
single data point** — the community `undoarb.bat`, which copies `abl` / `xbl` /
`xbl_config` / `xbl_ramdump` into `FASTBOOTD_FILES_HERE` and `recovery.img` into
`FASTBOOT_FILES_HERE`. It fits both of those, but it was never checked against an actual
SuperHybrid package. Verify it against yours before using that layout.

The fastbootd partition set in `flash.sh` (`system`, `system_ext`, `system_dlkm`,
`product`, `vendor`, `vendor_dlkm`, `odm`) follows the standard dynamic-partition
layout, but likewise was not tested against a device.

### Validation

Verified end-to-end against v12.11 (target) + v12.5 (donor): `xbl`, `xbl_config`,
`abl`, `xbl_ramdump` in the output are byte-identical to the donor; `tz`, `hyp`,
`devcfg` are byte-identical to the target; the produced set reads ARB 0 under
`arbcheck.py`. All four refusal paths were exercised.

**Static verification only.** This proves no image declares ARB >= 1. It does not
prove the donor's XBL boots the target's `tz` / `hyp` / `devcfg`. Have MSM/EDL ready.

## 8. Building from source: fixing ARB at the root

crDroid does not generate the firmware images — it ships them as prebuilt blobs from
the vendor tree. `device/oneplus/waffle/proprietary-firmware.txt` lists all 25 of them
(including `xbl_config.img`) and pins them to **OnePlus 12 CPH2573_16.0.9.400(EX01)**.
The blobs themselves live in `TheMuppets/proprietary_vendor_oneplus_waffle`, branch
`lineage-23.2`, under `radio/`, stored as Git LFS objects.

**The ARB1 blob is that vendor file.** Fetched from TheMuppets' LFS endpoint
(`lfs.undocumented.software`) and checked:

```
vendor/oneplus/waffle/radio/xbl_config.img
  sha256 bd086baa2cc15d37fb492dce0a959e785fabdede886061894f731524f5fda38b   ARB 1
```

That is byte-identical to the `xbl_config` inside the released
`crDroidAndroid-16.0-20260809-waffle-v12.11.zip`. The ARB0 blob from v12.5 is
`sha256 9ca6e36eee0a171502180dbc2f1b612c9dbf95e48c8115894e3870a99c9f0f1e`.

So when building from source, the fix is a one-file replacement **before** the build:

```bash
python3 arbswap.py --firmware-only -o /tmp/arb0 v12.11.zip v12.5.zip
cp /tmp/arb0/xbl_config.img vendor/oneplus/waffle/radio/xbl_config.img
python3 arbcheck.py vendor/oneplus/waffle/radio/xbl_config.img   # must print ARB = 0
```

The resulting ROM is ARB0 **by construction**, and `arbswap.py` is not needed on the
output. Verify the built zip anyway:

```bash
python3 arbcheck.py out/target/product/waffle/crDroidAndroid-*.zip
```

### The vendor tree pins a SHA1 for every blob

Swapping the file alone is **not** enough. `vendor/oneplus/waffle/Android.mk` registers
each firmware image with an integrity check:

```make
$(call add-radio-file-sha1-checked,radio/xbl_config.img,cb484b8fc21785c1836055421eccee452fdb604e)
```

Replacing a blob without updating its pin fails the build during the kati phase:

```
vendor/oneplus/waffle/Android.mk:9: error: vendor/oneplus/waffle/radio/abl.img
    SHA1 mismatch (f02a57e2af169b2c20a670e272b05ded2f1f72ca
                != 175a3d801430b94c0097a2c89406447b0dca3a92).
kati failed with: exit status 1
```

Note it is **SHA-1**, not the SHA-256 used everywhere else in this document. Recompute
the pins from the files on disk after swapping:

```bash
python3 - <<'EOF'
import re, hashlib, os
p = '/aosp/vendor/oneplus/waffle/Android.mk'
root = os.path.dirname(p)
def fix(m):
    rel = m.group(1)
    h = hashlib.sha1(open(os.path.join(root, rel), 'rb').read()).hexdigest()
    return f"$(call add-radio-file-sha1-checked,{rel},{h})"
src = open(p).read()
open(p, 'w').write(
    re.sub(r'\$\(call add-radio-file-sha1-checked,([^,]+),([0-9a-f]{40})\)', fix, src))
EOF
```

Recomputing every pin rather than just the swapped ones makes this idempotent and
self-healing. The header says "Automatically generated file. DO NOT MODIFY" - it is
produced by `setup-makefiles.py` from `proprietary-firmware.txt`, and re-running that
script would regenerate it with the upstream hashes, undoing the edit.

### Swap the whole bootloader group, not just `xbl_config`

v12.5 and v12.11 are different OOS firmware bases: **every** image differs between them
(all 19 checked; only `oplus_sec` is byte-identical). Replacing `xbl_config` alone would
therefore pair v12.11's `xbl` with v12.5's `xbl_config` - the DDR-training / clock-config
mismatch warned about in section 5. Swap the coherent group instead:

```
xbl.img  xbl_config.img  xbl_ramdump.img  abl.img
```

Note that `repo sync` will restore the LFS blobs to the upstream ARB1 versions and
`Android.mk` to its upstream pins, so both edits must be redone after every sync.

## 9. Reproducing the check by hand

```bash
# extract just xbl_config from an OTA zip
python3 payload_dumper.py --out out --images xbl_config <rom.zip>

# the ARB field, if the hash segment sits at 0x38000 (verify per-image)
xxd -s 0x38048 -l 4 out/xbl_config.img
#   00 00 00 00  -> ARB 0, safe
#   01 00 00 00  -> ARB 1, will blow the fuse
```

The `0x38048` offset is **not** a constant — it is
`hash_segment_offset + 40 + common_metadata_size + qti_metadata_size + 8` and must be
recomputed per image by walking the ELF program headers. `arbcheck.py` does this
properly and prints the computed offset as `arb_file_offset` in `--json` mode.
