#!/usr/bin/env python3
"""
arbcheck.py - report the Qualcomm anti-rollback (ARB) version of a ROM.

Accepts an A/B OTA zip, a bare payload.bin, or a raw firmware .img.
Reads the ARB field out of the signed MBN v7 OEM metadata block.
No third-party dependencies (zstd support is optional).

See ARB.md for the format analysis this is based on.

Exit codes:  0 = ARB 0 (safe)   1 = ARB >= 1 (will blow the fuse)   2 = undetermined
"""

import argparse
import bz2
import json
import lzma
import os
import re
import struct
import sys
import zipfile

BLOCK_DEFAULT = 4096
HASH_SEGMENT_TYPE = 2      # (p_flags >> 24) & 0xff
MBN_HEADER_SIZE = 40
MBN_VERSION = 7
ARB_OFFSET_IN_OEM_META = 8

# Partitions that are never signed Qualcomm firmware. Everything else that is
# small enough gets inspected, so a newly-added firmware partition is picked up
# automatically rather than being missed by a stale allowlist.
NON_FIRMWARE = {
    "system", "system_ext", "system_dlkm", "product", "vendor", "vendor_dlkm",
    "odm", "odm_dlkm", "boot", "init_boot", "vendor_boot", "recovery", "dtbo",
    "vbmeta", "vbmeta_system", "vbmeta_vendor", "super", "userdata", "metadata",
    "splash", "engineering_cdt", "oplusstanvbk", "cache", "persist",
}

OP_NAMES = {
    0: "REPLACE", 1: "REPLACE_BZ", 2: "MOVE", 3: "BSDIFF", 4: "SOURCE_COPY",
    5: "SOURCE_BSDIFF", 6: "ZERO", 7: "DISCARD", 8: "REPLACE_XZ", 9: "PUFFDIFF",
    10: "BROTLI_BSDIFF", 11: "ZUCCHINI", 12: "LZ4DIFF_BSDIFF",
    13: "LZ4DIFF_PUFFDIFF", 14: "ZSTD",
}
FULL_OPS = {0, 1, 6, 8, 14}


class ArbError(Exception):
    pass


# --------------------------------------------------------------------------
# minimal protobuf wire-format reader
# --------------------------------------------------------------------------

def _varint(buf, i):
    shift = 0
    val = 0
    while True:
        if i >= len(buf):
            raise ArbError("truncated varint in manifest")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 70:
            raise ArbError("malformed varint in manifest")


def _pb_fields(buf):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _varint(buf, i)
        fnum, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = _varint(buf, i)
            yield fnum, wtype, val
        elif wtype == 1:
            yield fnum, wtype, buf[i:i + 8]
            i += 8
        elif wtype == 2:
            ln, i = _varint(buf, i)
            yield fnum, wtype, buf[i:i + ln]
            i += ln
        elif wtype == 5:
            yield fnum, wtype, buf[i:i + 4]
            i += 4
        else:
            raise ArbError("unsupported protobuf wire type %d" % wtype)


def _parse_extents(buf):
    start = num = 0
    for fnum, _wt, val in _pb_fields(buf):
        if fnum == 1:
            start = val
        elif fnum == 2:
            num = val
    return start, num


def _parse_operation(buf):
    op = {"type": 0, "data_offset": 0, "data_length": 0, "dst": []}
    for fnum, wt, val in _pb_fields(buf):
        if fnum == 1 and wt == 0:
            op["type"] = val
        elif fnum == 2 and wt == 0:
            op["data_offset"] = val
        elif fnum == 3 and wt == 0:
            op["data_length"] = val
        elif fnum == 6 and wt == 2:
            op["dst"].append(_parse_extents(val))
    return op


def _parse_partition(buf):
    part = {"name": None, "size": None, "ops": []}
    for fnum, wt, val in _pb_fields(buf):
        if fnum == 1 and wt == 2:
            part["name"] = val.decode("utf-8", "replace")
        elif fnum == 7 and wt == 2:            # new_partition_info
            for f2, w2, v2 in _pb_fields(val):
                if f2 == 1 and w2 == 0:        # PartitionInfo.size
                    part["size"] = v2
        elif fnum == 8 and wt == 2:
            part["ops"].append(_parse_operation(val))
    return part


def parse_manifest(buf):
    block_size = BLOCK_DEFAULT
    parts = []
    for fnum, wt, val in _pb_fields(buf):
        if fnum == 3 and wt == 0:
            block_size = val
        elif fnum == 13 and wt == 2:
            parts.append(_parse_partition(val))
    return block_size, parts


# --------------------------------------------------------------------------
# payload.bin access
# --------------------------------------------------------------------------

def locate_payload_in_zip(path):
    """Return (file_handle, absolute_offset) for a STORED payload.bin."""
    zf = zipfile.ZipFile(path)
    try:
        info = zf.getinfo("payload.bin")
    except KeyError:
        raise ArbError("no payload.bin in %s (not an A/B OTA zip?)" % os.path.basename(path))
    finally:
        zf.close()

    if info.compress_type != zipfile.ZIP_STORED:
        raise ArbError("payload.bin is compressed inside the zip; cannot seek into it")

    fh = open(path, "rb")
    fh.seek(info.header_offset)
    lfh = fh.read(30)
    if lfh[:4] != b"PK\x03\x04":
        fh.close()
        raise ArbError("bad local file header for payload.bin")
    name_len, extra_len = struct.unpack("<HH", lfh[26:30])
    return fh, info.header_offset + 30 + name_len + extra_len


def read_payload(fh, base):
    """Parse the payload header. Returns (block_size, partitions, data_start)."""
    fh.seek(base)
    head = fh.read(24)
    if head[:4] != b"CrAU":
        raise ArbError("bad payload magic %r (expected CrAU)" % head[:4])
    version, manifest_size = struct.unpack(">QQ", head[4:20])
    sig_size, = struct.unpack(">I", head[20:24])
    if version != 2:
        raise ArbError("unsupported payload version %d" % version)
    manifest = fh.read(manifest_size)
    if len(manifest) != manifest_size:
        raise ArbError("truncated manifest")
    block_size, parts = parse_manifest(manifest)
    return block_size, parts, base + 24 + manifest_size + sig_size


def _decompress(op, raw):
    t = op["type"]
    if t == 0:
        return raw
    if t == 1:
        return bz2.decompress(raw)
    if t == 8:
        return lzma.decompress(raw)
    if t == 14:
        try:
            import zstandard
        except ImportError:
            raise ArbError("payload uses ZSTD operations; pip install zstandard")
        return zstandard.ZstdDecompressor().decompress(raw)
    raise ArbError("unsupported operation %s (incremental OTA?)" % OP_NAMES.get(t, t))


def build_partition(fh, data_start, block_size, part):
    for op in part["ops"]:
        if op["type"] not in FULL_OPS:
            raise ArbError("partition %s uses %s - incremental payloads are not "
                           "self-contained and cannot be checked"
                           % (part["name"], OP_NAMES.get(op["type"], op["type"])))
    buf = bytearray(part["size"] or 0)
    for op in part["ops"]:
        out = None
        if op["type"] != 6:                    # 6 = ZERO, no payload data
            fh.seek(data_start + op["data_offset"])
            raw = fh.read(op["data_length"])
            if len(raw) != op["data_length"]:
                raise ArbError("truncated payload data for %s" % part["name"])
            out = _decompress(op, raw)
        pos = 0
        for start_block, num_blocks in op["dst"]:
            start = start_block * block_size
            span = num_blocks * block_size
            chunk = b"\0" * span if out is None else out[pos:pos + span]
            if out is not None:
                pos += span
            end = start + len(chunk)
            if end > len(buf):
                buf.extend(b"\0" * (end - len(buf)))
            buf[start:end] = chunk
    return bytes(buf)


# --------------------------------------------------------------------------
# MBN v7 parsing
# --------------------------------------------------------------------------

def _program_headers(d, base):
    if d[base:base + 4] != b"\x7fELF":
        return None
    ei_class, ei_data = d[base + 4], d[base + 5]
    if ei_class not in (1, 2) or ei_data != 1:      # 32/64-bit, little-endian
        return None
    try:
        if ei_class == 2:
            phoff, = struct.unpack_from("<Q", d, base + 0x20)
            phentsize, = struct.unpack_from("<H", d, base + 0x36)
            phnum, = struct.unpack_from("<H", d, base + 0x38)
        else:
            phoff, = struct.unpack_from("<I", d, base + 0x1C)
            phentsize, = struct.unpack_from("<H", d, base + 0x2A)
            phnum, = struct.unpack_from("<H", d, base + 0x2C)
    except struct.error:
        return None
    if not phnum or phnum > 1024 or phentsize not in (0x20, 0x38):
        return None
    out = []
    for i in range(phnum):
        off = base + phoff + i * phentsize
        try:
            if ei_class == 2:
                _pt, pflags = struct.unpack_from("<II", d, off)
                poff, _va, _pa, pfsz, _msz = struct.unpack_from("<QQQQQ", d, off + 8)
            else:
                _pt, poff, _va, _pa, pfsz, _msz, pflags = struct.unpack_from("<IIIIIII", d, off)
        except struct.error:
            return None
        out.append((pflags, poff, pfsz))
    return out


def parse_mbn(d, base):
    """Parse the MBN v7 hash segment of the ELF at `base`. Returns a dict or None."""
    phdrs = _program_headers(d, base)
    if not phdrs:
        return None
    hashseg = None
    for pflags, poff, pfsz in phdrs:
        if (pflags >> 24) & 0xFF == HASH_SEGMENT_TYPE and 0 < pfsz <= 0x100000:
            hashseg = (poff, pfsz)
            break
    if hashseg is None:
        return None

    seg_off, seg_size = hashseg
    hs = d[base + seg_off: base + seg_off + seg_size]
    if len(hs) < MBN_HEADER_SIZE:
        return {"status": "truncated hash segment"}

    ver, = struct.unpack_from("<I", hs, 4)
    if ver != MBN_VERSION:
        return {"status": "unsupported MBN header version %d" % ver}

    (cm_sz, qti_md_sz, oem_md_sz, ht_sz,
     qti_sig_sz, qti_cc_sz, oem_sig_sz, oem_cc_sz) = struct.unpack_from("<8I", hs, 8)

    total = (MBN_HEADER_SIZE + cm_sz + qti_md_sz + oem_md_sz + ht_sz
             + qti_sig_sz + qti_cc_sz + oem_sig_sz + oem_cc_sz)
    if total > seg_size or (seg_size - total) >= BLOCK_DEFAULT:
        return {"status": "MBN size fields do not match segment (%d vs %d)" % (total, seg_size)}

    cm_off = MBN_HEADER_SIZE
    om_off = cm_off + cm_sz + qti_md_sz
    cm = hs[cm_off:cm_off + cm_sz]
    om = hs[om_off:om_off + oem_md_sz]

    if len(om) < ARB_OFFSET_IN_OEM_META + 4:
        return {"status": "OEM metadata too small (%d bytes)" % len(om)}

    sw_id = struct.unpack_from("<Q", cm, 8)[0] if len(cm) >= 16 else None
    arb, = struct.unpack_from("<I", om, ARB_OFFSET_IN_OEM_META)

    return {
        "status": "ok",
        "sw_id": sw_id,
        "arb": arb,
        "elf_offset": base,
        "arb_file_offset": base + seg_off + om_off + ARB_OFFSET_IN_OEM_META,
        "hash_algo_bytes": ht_sz // len(phdrs) if len(phdrs) and ht_sz % len(phdrs) == 0 else None,
    }


def scan_image(data):
    """Find every signed sub-image in a partition (xbl nests several ELFs)."""
    results = []
    for m in re.finditer(b"\x7fELF", data):
        r = parse_mbn(data, m.start())
        if r is not None:
            results.append(r)
    return results


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def check_zip_or_payload(path, size_cap, only=None):
    if zipfile.is_zipfile(path):
        fh, base = locate_payload_in_zip(path)
    else:
        fh, base = open(path, "rb"), 0
    with fh:
        block_size, parts, data_start = read_payload(fh, base)
        report = []
        for part in sorted(parts, key=lambda p: p["name"] or ""):
            name = part["name"]
            size = part["size"] or 0
            if only and name not in only:
                continue
            if not only:
                if name in NON_FIRMWARE or size > size_cap:
                    continue
            entry = {"partition": name, "size": size, "images": [], "error": None}
            try:
                entry["images"] = scan_image(build_partition(fh, data_start, block_size, part))
            except ArbError as e:
                entry["error"] = str(e)
            report.append(entry)
    return report


def check_raw_image(path):
    with open(path, "rb") as fh:
        data = fh.read()
    name = os.path.basename(path)
    for ext in (".img", ".bin", ".mbn", ".elf"):
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    return [{"partition": name, "size": len(data),
             "images": scan_image(data), "error": None}]


def verdict(report, raw=False):
    """Return (code, text). Fails loud: an unreadable xbl_config is never 'safe'."""
    by_name = {e["partition"]: e for e in report}
    values, unknown = [], []
    for e in report:
        if e["error"]:
            unknown.append("%s: %s" % (e["partition"], e["error"]))
            continue
        for img in e["images"]:
            if img["status"] == "ok":
                values.append(img["arb"])
            else:
                unknown.append("%s: %s" % (e["partition"], img["status"]))

    xc = by_name.get("xbl_config")
    if xc is None:
        if any(e["images"] for e in report):
            pass                     # firmware present, just no xbl_config partition
        elif raw:
            # A raw file was handed to us specifically to be read. Finding no signed
            # image in it means we learned nothing - it is empty, truncated, or not
            # an MBN image. That is not the same as "this package ships no firmware".
            return 2, ("Could NOT determine ARB: no signed Qualcomm image found in "
                       "this file. It is empty, truncated, or not an MBN image.")
        else:
            return 0, ("No signed firmware found in this package - flashing it cannot "
                       "change the ARB fuse.")
    elif xc["error"] or not any(i["status"] == "ok" for i in xc["images"]):
        return 2, ("Could NOT determine ARB: xbl_config is present but unreadable. "
                   "Do not assume this is safe.\n  " + "\n  ".join(unknown))

    if not values:
        return 2, ("Could NOT determine ARB - no image parsed successfully.\n  "
                   + "\n  ".join(unknown))

    top = max(values)
    if top >= 1:
        return 1, ("ARB = %d  ***  Flashing this WILL blow the anti-rollback fuse and "
                   "permanently lock out every ARB%d firmware set." % (top, top - 1))
    return 0, "ARB = 0  -  safe, this will not blow the anti-rollback fuse."


def main():
    ap = argparse.ArgumentParser(
        description="Report the Qualcomm anti-rollback (ARB) version of a ROM zip, "
                    "payload.bin, or raw firmware image.")
    ap.add_argument("target", nargs="+", help="OTA .zip, payload.bin, or .img")
    ap.add_argument("-a", "--all", action="store_true",
                    help="also inspect large partitions (modem, dsp, ...)")
    ap.add_argument("-p", "--partition", action="append",
                    help="inspect only this partition (repeatable)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list every partition, including unsigned ones")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    size_cap = 1 << 40 if args.all else 16 << 20
    worst = 0
    blob = []

    for target in args.target:
        try:
            if not os.path.exists(target):
                raise ArbError("no such file")
            raw = not (zipfile.is_zipfile(target)
                       or open(target, "rb").read(4) == b"CrAU")
            report = check_raw_image(target) if raw \
                else check_zip_or_payload(target, size_cap, args.partition)
            code, text = verdict(report, raw=raw)
        except ArbError as e:
            report, code, text = [], 2, "Could NOT determine ARB: %s" % e

        # 1 (fuse will blow) outranks 2 (undetermined) outranks 0 (safe)
        worst = max(worst, code, key=lambda c: {0: 0, 2: 1, 1: 2}[c])

        if args.json:
            blob.append({"target": target, "exit_code": code,
                         "verdict": text, "partitions": report})
            continue

        print("=" * 78)
        print(os.path.basename(target))
        print("=" * 78)
        rows = []
        for e in report:
            if e["error"]:
                rows.append((e["partition"], "-", "ERROR", e["error"]))
            elif not e["images"]:
                if args.verbose:
                    rows.append((e["partition"], "-", "-", "no signed image"))
            else:
                for img in e["images"]:
                    if img["status"] != "ok":
                        rows.append((e["partition"], "-", "UNKNOWN", img["status"]))
                    else:
                        note = "" if img["arb"] == 0 else "<== ARB SET"
                        rows.append((e["partition"],
                                     "0x%x" % img["sw_id"] if img["sw_id"] is not None else "-",
                                     str(img["arb"]), note))
        if rows:
            print("  %-16s %-20s %-8s %s" % ("partition", "sw_id", "arb", ""))
            print("  " + "-" * 74)
            for r in rows:
                print("  %-16s %-20s %-8s %s" % r)
        print()
        print("  " + text.replace("\n", "\n  "))
        print()

    if args.json:
        print(json.dumps(blob, indent=2))
    return worst


if __name__ == "__main__":
    sys.exit(main())
