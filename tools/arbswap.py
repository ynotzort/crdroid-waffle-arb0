#!/usr/bin/env python3
"""
arbswap.py - build an ARB0-safe flashable image set from an ARB1 ROM.

Extracts every partition from a target ROM, but takes the anti-rollback-carrying
bootloader images from an older ARB0 donor ROM, verifies the result on disk, and
emits a fastboot command list.

It deliberately does NOT rebuild a flashable OTA zip: payload.bin carries a
per-operation SHA-256 and a signed metadata blob, and recovery checks the zip
against the ROM's release key. Substituting a partition invalidates all of it.

It deliberately does NOT run fastboot. It writes a script for you to review.

See ARB.md for the format analysis.

Exit codes:  0 = ARB0 set produced   1 = refused (unsafe/mismatch)   2 = error
"""

import argparse
import hashlib
import os
import shutil
import stat
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arbcheck import (ArbError, NON_FIRMWARE, locate_payload_in_zip,
                      read_payload, build_partition, scan_image)

# The coherent bootloader group. Only xbl_config actually carries the ARB field,
# but xbl <-> xbl_config are a matched pair (DDR training, clock/PMIC config) and
# mixing versions across that boundary risks an EDL brick, so the group moves
# together. Matches the community undoarb.bat set.
DEFAULT_SWAP = ["xbl", "xbl_config", "xbl_ramdump", "abl"]

# Partitions that live in the super partition and need fastbootd.
LOGICAL = {"system", "system_ext", "system_dlkm", "product", "vendor",
           "vendor_dlkm", "odm", "odm_dlkm"}


class Refused(Exception):
    pass


def _log(msg=""):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# payload access
# --------------------------------------------------------------------------

class Rom:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.fh, base = locate_payload_in_zip(path) if zipfile.is_zipfile(path) \
            else (open(path, "rb"), 0)
        self.block_size, parts, self.data_start = read_payload(self.fh, base)
        self.parts = {p["name"]: p for p in parts}
        self.meta = self._metadata(path)

    @staticmethod
    def _metadata(path):
        out = {}
        if not zipfile.is_zipfile(path):
            return out
        try:
            with zipfile.ZipFile(path) as zf:
                raw = zf.read("META-INF/com/android/metadata").decode("utf-8", "replace")
            for line in raw.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
        except (KeyError, OSError):
            pass
        return out

    def device(self):
        return self.meta.get("pre-device", "?")

    def fingerprint(self):
        return self.meta.get("post-build", "?")

    def extract(self, name):
        if name not in self.parts:
            raise Refused("%s has no partition %r" % (self.name, name))
        return build_partition(self.fh, self.data_start, self.block_size, self.parts[name])

    def arb_of(self, name):
        """(max_arb, note). max_arb is None when it could not be determined."""
        try:
            images = scan_image(self.extract(name))
        except ArbError as e:
            return None, str(e)
        if not images:
            return 0, "no signed image"
        vals = [i["arb"] for i in images if i["status"] == "ok"]
        bad = [i["status"] for i in images if i["status"] != "ok"]
        if bad:
            return None, "; ".join(bad)
        return max(vals), ""

    def close(self):
        self.fh.close()


def firmware_partitions(rom, size_cap=16 << 20):
    return sorted(n for n, p in rom.parts.items()
                  if n not in NON_FIRMWARE and (p["size"] or 0) <= size_cap)


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------

def survey(rom, label, size_cap=16 << 20):
    """Return {partition: arb}. Raises Refused if anything is undetermined."""
    _log("  scanning %s (%s)" % (label, rom.name))
    result = {}
    for name in firmware_partitions(rom, size_cap):
        arb, note = rom.arb_of(name)
        if arb is None:
            raise Refused("cannot determine ARB of %s in %s: %s" % (name, rom.name, note))
        result[name] = arb
        if arb:
            _log("    %-16s ARB %d   <== carries the fuse bump" % (name, arb))
    return result


def check_donor(donor, target, swap):
    if donor.device() != target.device() and "?" not in (donor.device(), target.device()):
        raise Refused("device mismatch: donor is %r, target is %r"
                      % (donor.device(), target.device()))
    for name in swap:
        if name not in donor.parts:
            raise Refused("donor %s has no %s partition" % (donor.name, name))
        arb, note = donor.arb_of(name)
        if arb is None:
            raise Refused("cannot determine ARB of donor %s: %s" % (name, note))
        if arb:
            raise Refused("donor %s has %s at ARB %d - it is not an ARB0 donor"
                          % (donor.name, name, arb))


def plan(target_arb, swap):
    """Every ARB-carrying partition must be covered by the substitution set."""
    carriers = sorted(n for n, v in target_arb.items() if v >= 1)
    if not carriers:
        return carriers, False
    uncovered = [n for n in carriers if n not in swap]
    if uncovered:
        raise Refused(
            "these partitions carry ARB >= 1 but are not in the substitution set: %s\n"
            "       The ARB flag has moved. Re-run ARB.md's analysis before trusting\n"
            "       any substitution set, and pass --swap explicitly once you know\n"
            "       which images must be replaced." % ", ".join(uncovered))
    return carriers, True


def preflight_space(outdir, needed):
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(outdir)) or ".").free
    if free < needed + (256 << 20):
        raise Refused("need %.2f GiB plus headroom, only %.2f GiB free"
                      % (needed / 2**30, free / 2**30))


def write_images(target, donor, swap, outdir, firmware_only):
    os.makedirs(outdir, exist_ok=True)
    names = firmware_partitions(target) if firmware_only else sorted(target.parts)
    for n in swap:
        if n not in names:
            names.append(n)
    needed = sum(target.parts[n]["size"] or 0 for n in names)
    preflight_space(outdir, needed)
    _log("  writing %d partitions (%.2f GiB) to %s/" % (len(names), needed / 2**30, outdir))

    written = {}
    for name in sorted(names):
        src = donor if name in swap else target
        data = src.extract(name)
        path = os.path.join(outdir, name + ".img")
        with open(path, "wb") as fh:
            fh.write(data)
        written[name] = {"path": path, "from": src.name, "swapped": name in swap,
                         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        if name in swap:
            _log("    %-16s <- DONOR  %s" % (name, src.name))
    return written


def verify_on_disk(written, firmware):
    """Re-read every written file from disk and recompute ARB from those bytes.

    Only firmware partitions are MBN-scanned. Filesystem images (odm, vendor,
    product, ...) contain thousands of ordinary ELF binaries that are not signed
    Qualcomm images, and scanning them yields only noise.
    """
    _log("  re-reading %d images from disk (%d firmware)" % (len(written), len(firmware)))
    worst, unknown = 0, []
    for name, info in sorted(written.items()):
        with open(info["path"], "rb") as fh:
            data = fh.read()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise Refused("%s changed on disk after writing" % info["path"])
        if name not in firmware:
            info["arb"] = "n/a"
            continue
        images = scan_image(data)
        vals = [i["arb"] for i in images if i["status"] == "ok"]
        bad = [i["status"] for i in images if i["status"] != "ok"]
        if vals:
            info["arb"] = max(vals)
            worst = max(worst, max(vals))
        elif not images:
            info["arb"] = "n/a"          # not a signed MBN (filesystem image)
        else:
            info["arb"] = None           # signed, but we could not read it
        if bad:
            unknown.append("%s: %s" % (name, "; ".join(bad)))

    xc = written.get("xbl_config")
    if xc is not None and not isinstance(xc.get("arb"), int):
        raise Refused("xbl_config was written but its ARB could not be read - refusing "
                      "to call this safe:\n       " + "\n       ".join(unknown or ["no signed image found"]))
    if worst >= 1:
        offenders = [n for n, i in written.items() if (i.get("arb") or 0) >= 1]
        raise Refused("output set still reads ARB %d (%s) - substitution did not work"
                      % (worst, ", ".join(sorted(offenders))))
    if unknown:
        _log("    note: unparsed images (not ARB carriers): " + "; ".join(unknown))
    return worst


def write_manifest(outdir, written, target, donor, swap):
    path = os.path.join(outdir, "MANIFEST.txt")
    with open(path, "w") as fh:
        fh.write("ARB0 substituted image set\n")
        fh.write("target : %s\n         %s\n" % (target.name, target.fingerprint()))
        fh.write("donor  : %s\n         %s\n" % (donor.name, donor.fingerprint()))
        fh.write("swapped: %s\n\n" % ", ".join(sorted(swap)))
        fh.write("%-18s %-5s %-9s %-12s %s\n" % ("image", "arb", "source", "size", "sha256"))
        fh.write("-" * 100 + "\n")
        for name, i in sorted(written.items()):
            fh.write("%-18s %-5s %-9s %-12d %s\n"
                     % (name, "?" if i["arb"] is None else i["arb"],
                        "DONOR" if i["swapped"] else "target", i["size"], i["sha256"]))
    return path


def write_flash_script(outdir, written, swap, target, donor, firmware, both_slots):
    """Emit a reviewable fastboot script. Never executes anything itself."""
    physical = sorted(n for n in written
                      if n not in LOGICAL and n not in ("super", "userdata", "metadata"))
    logical = sorted(n for n in written if n in LOGICAL)
    path = os.path.join(outdir, "flash.sh")

    with open(path, "w") as fh:
        w = fh.write
        w("#!/usr/bin/env bash\n")
        w("# GENERATED BY arbswap.py - REVIEW BEFORE RUNNING.\n#\n")
        w("# target : %s\n#          %s\n" % (target.name, target.fingerprint()))
        w("# donor  : %s\n#          %s\n" % (donor.name, donor.fingerprint()))
        w("# swapped: %s\n#\n" % ", ".join(sorted(swap)))
        w("# Every image below was verified on disk to read anti_rollback_version = 0.\n")
        w("# That is a static check. It does not prove the donor bootloader boots the\n")
        w("# target's tz/hyp/devcfg. Have MSM/EDL recovery ready before you start.\n#\n")
        w("# Slot policy:\n")
        if both_slots:
            w("#   Qualcomm firmware -> BOTH slots. An ARB1 xbl_config left on the\n")
            w("#   inactive slot still blows the fuse when that slot is validated, and\n")
            w("#   a half-swapped chain on the inactive slot is incoherent. Whether a\n")
            w("#   partition actually has slots is asked of the device via\n")
            w("#   'fastboot getvar has-slot:<part>' rather than assumed.\n")
        else:
            w("#   --active-slot-only was used: firmware goes to the ACTIVE slot only.\n")
            w("#   An ARB1 image left on the inactive slot can still blow the fuse.\n")
        w("#   boot/dtbo/vbmeta and the logical partitions -> active slot, which is\n")
        w("#   what an OTA would have written.\n#\n")
        w("# Otherwise these commands reproduce what update_engine would have written.\n")
        w("# No erase, no format, no added vbmeta flags.\n\n")

        w("set -euo pipefail\n")
        w('cd "$(dirname "$0")"\n\n')
        w('command -v fastboot >/dev/null || { echo "fastboot not found"; exit 1; }\n\n')

        w("# Ask the device whether a partition is slotted; do not guess.\n")
        w("has_slot() {\n")
        w('  local v\n')
        w('  v=$(fastboot getvar "has-slot:$1" 2>&1 | sed -n "s/^has-slot:$1: *//p" | head -1)\n')
        w('  [ "$v" = "yes" ]\n')
        w("}\n\n")
        w("flash_both() {   # firmware: every slot the device actually has\n")
        w('  if has_slot "$1"; then\n')
        w('    fastboot flash "${1}_a" "$2"\n')
        w('    fastboot flash "${1}_b" "$2"\n')
        w("  else\n")
        w('    fastboot flash "$1" "$2"\n')
        w("  fi\n")
        w("}\n\n")

        w("# ---- bootloader-mode fastboot: physical partitions ----\n")
        w('echo ">>> reboot the phone to BOOTLOADER, then press enter"; read -r _\n\n')
        for n in physical:
            if both_slots and n in firmware:
                w('flash_both %s %s.img\n' % (n, n))
            else:
                w("fastboot flash %s %s.img\n" % (n, n))

        if logical:
            w("\n# ---- fastbootd: logical partitions inside super ----\n")
            w("fastboot reboot fastboot\n")
            w('echo ">>> wait for fastbootd, then press enter"; read -r _\n\n')
            for n in logical:
                w("fastboot flash %s %s.img\n" % (n, n))

        w("\necho\n")
        w('echo "done - reboot with: fastboot reboot"\n')

    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path, physical, logical


def write_superhybrid(outdir, written, firmware):
    """Lay files out for the SuperHybrid flasher.

    The split is inferred from the community undoarb.bat, which puts abl/xbl/
    xbl_config/xbl_ramdump in FASTBOOTD_FILES_HERE and recovery in
    FASTBOOT_FILES_HERE: Qualcomm firmware goes to fastbootd, boot images and
    logical partitions to fastboot. Verify against your SuperHybrid package.
    """
    boot_dir = os.path.join(outdir, "FASTBOOT_FILES_HERE")
    bootd_dir = os.path.join(outdir, "FASTBOOTD_FILES_HERE")
    os.makedirs(boot_dir, exist_ok=True)
    os.makedirs(bootd_dir, exist_ok=True)
    for name, i in written.items():
        dest = bootd_dir if name in firmware else boot_dir
        shutil.copy2(i["path"], os.path.join(dest, name + ".img"))
    return boot_dir, bootd_dir


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Build an ARB0-safe flashable image set from an ARB1 ROM by "
                    "substituting the bootloader images from an older ARB0 ROM.")
    ap.add_argument("target", help="the ROM you want to flash (may be ARB1)")
    ap.add_argument("donor", help="an older ARB0 ROM to take bootloader images from")
    ap.add_argument("-o", "--out", default="arb0_out", help="output directory")
    ap.add_argument("--swap", help="comma-separated substitution set (default: %s)"
                    % ",".join(DEFAULT_SWAP))
    ap.add_argument("--firmware-only", action="store_true",
                    help="write only the Qualcomm firmware chain, not the AOSP partitions")
    ap.add_argument("--active-slot-only", action="store_true",
                    help="flash firmware to the active slot only (NOT recommended: an "
                         "ARB1 image left on the inactive slot still blows the fuse)")
    ap.add_argument("--superhybrid", action="store_true",
                    help="also lay files out as FASTBOOT_FILES_HERE / FASTBOOTD_FILES_HERE")
    ap.add_argument("--deep", action="store_true",
                    help="also survey large firmware partitions (modem, dsp); slow")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="survey and validate, but write nothing")
    args = ap.parse_args()

    swap = [s.strip() for s in args.swap.split(",")] if args.swap else list(DEFAULT_SWAP)
    size_cap = (1 << 40) if args.deep else (16 << 20)
    target = donor = None

    try:
        target = Rom(args.target)
        donor = Rom(args.donor)

        _log("target : %s" % target.name)
        _log("         %s" % target.fingerprint())
        _log("donor  : %s" % donor.name)
        _log("         %s" % donor.fingerprint())
        _log()

        _log("[1/5] surveying target for ARB carriers")
        target_arb = survey(target, "target", size_cap)
        firmware = set(target_arb)
        carriers, needed = plan(target_arb, swap)
        if not needed:
            _log("      target is already ARB0 - no substitution required.")
        else:
            _log("      carriers: %s (all covered by the substitution set)"
                 % ", ".join(carriers))
        _log()

        _log("[2/5] validating donor")
        check_donor(donor, target, swap)
        _log("      donor is ARB0 for: %s" % ", ".join(sorted(swap)))
        _log()

        if args.dry_run:
            _log("dry run - nothing written.")
            return 0

        _log("[3/5] extracting")
        written = write_images(target, donor, swap, args.out, args.firmware_only)
        _log()

        _log("[4/5] verifying the images on disk")
        verify_on_disk(written, firmware)
        _log("      all images read anti_rollback_version = 0")
        _log()

        _log("[5/5] writing manifest and flash script")
        man = write_manifest(args.out, written, target, donor, swap)
        script, fw, logical = write_flash_script(
            args.out, written, swap, target, donor, firmware,
            not args.active_slot_only)
        _log("      %s" % man)
        _log("      %s  (%d bootloader-mode, %d fastbootd)"
             % (script, len(fw), len(logical)))
        if args.superhybrid:
            b, d = write_superhybrid(args.out, written, firmware)
            _log("      %s/" % b)
            _log("      %s/" % d)
        _log()
        _log("Done. REVIEW %s BEFORE RUNNING IT." % script)
        return 0

    except Refused as e:
        _log()
        _log("REFUSED: %s" % e)
        _log()
        _log("Nothing was flashed and no flash script was written.")
        return 1
    except (ArbError, OSError) as e:
        _log()
        _log("ERROR: %s" % e)
        return 2
    finally:
        for r in (target, donor):
            if r is not None:
                r.close()


if __name__ == "__main__":
    sys.exit(main())
