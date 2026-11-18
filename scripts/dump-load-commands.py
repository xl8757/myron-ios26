#!/usr/bin/env python3
"""Print the Mach-O load commands of kernel images so two builds can be compared.

Why this exists: `LC_SEGMENT_SPLIT_INFO` (0x1e) is what
`kits/XnuPrelink/fileset_layout.split_references` reads, and the RELEASE
comparison kernel came out without it, so `emit_images` stops with "Native
image has no split-segment relocation information". The DEVELOPMENT kernel does
carry it - the earlier prelink run that emitted an 85 MiB kernel.macho proves
that - so the two configurations differ in something `makedefs` decides.

XNU's `makedefs/MakeInc.def` puts the flag in

    LDFLAGS_KERNEL_ONLY_SUBARCH_arm64e = -Wl,-add_split_seg_info -Wl,-kernel

and appends it through `LDFLAGS_KERNEL_ONLY`, which the same variable also uses
to create `__LINKINFO`. The RELEASE kernel has `__LINKINFO` but no
`LC_SEGMENT_SPLIT_INFO`, which narrows the question to how that one flag is
handled rather than to whether the variable reached the link at all.

make does not echo the full link line by default, so the load commands of the
produced image are the practical evidence. Comparing a DEVELOPMENT image with a
RELEASE image says immediately whether the flag is the difference.

Usage: dump-load-commands.py <image> [<image> ...]
"""

import struct
import sys
from pathlib import Path

NAMES = {
    0x1: "LC_SEGMENT",
    0x2: "LC_SYMTAB",
    0x3: "LC_SYMSEG",
    0x4: "LC_THREAD",
    0x5: "LC_UNIXTHREAD",
    0x6: "LC_LOADFVMLIB",
    0x7: "LC_IDFVMLIB",
    0x8: "LC_IDENT",
    0x9: "LC_FVMFILE",
    0xA: "LC_PREPAGE",
    0xB: "LC_DYSYMTAB",
    0xC: "LC_LOAD_DYLIB",
    0xD: "LC_ID_DYLIB",
    0xE: "LC_LOAD_DYLINKER",
    0xF: "LC_ID_DYLINKER",
    0x10: "LC_PREBOUND_DYLIB",
    0x11: "LC_ROUTINES",
    0x12: "LC_SUB_FRAMEWORK",
    0x13: "LC_SUB_UMBRELLA",
    0x14: "LC_SUB_CLIENT",
    0x15: "LC_SUB_LIBRARY",
    0x16: "LC_TWOLEVEL_HINTS",
    0x17: "LC_PREBIND_CKSUM",
    0x18: "LC_LOAD_WEAK_DYLIB",
    0x19: "LC_SEGMENT_64",
    0x1A: "LC_ROUTINES_64",
    0x1B: "LC_UUID",
    0x1D: "LC_CODE_SIGNATURE",
    0x1E: "LC_SEGMENT_SPLIT_INFO",
    0x20: "LC_LAZY_LOAD_DYLIB",
    0x21: "LC_ENCRYPTION_INFO",
    0x22: "LC_DYLD_INFO",
    0x23: "LC_DYLD_INFO_ONLY",
    0x24: "LC_VERSION_MIN_IPHONEOS",
    0x25: "LC_VERSION_MIN_MACOSX",
    0x26: "LC_FUNCTION_STARTS",
    0x27: "LC_DYLD_ENVIRONMENT",
    0x29: "LC_DATA_IN_CODE",
    0x2A: "LC_DYLIB_CODE_SIGN_DRS",
    0x2B: "LC_ENCRYPTION_INFO_64",
    0x2C: "LC_LINKER_OPTION",
    0x2D: "LC_LINKER_OPTIMIZATION_HINT",
    0x2E: "LC_VERSION_MIN_TVOS",
    0x2F: "LC_VERSION_MIN_WATCHOS",
    0x32: "LC_BUILD_VERSION",
    0x80000022: "LC_DYLD_CHAINED_FIXUPS",
    0x80000028: "LC_MAIN",
    0x80000029: "LC_SOURCE_VERSION",
    0x8000002A: "LC_DYLIB",
    0x80000033: "LC_DYLD_INFO_ONLY",
    0x80000034: "LC_DYLD_EXPORTS_TRIE",
}

FILE_TYPES = {2: "MH_EXECUTE", 6: "MH_DYLIB", 8: "MH_BUNDLE", 11: "MH_KEXT_BUNDLE",
              12: "MH_FILESET"}

CPU_TYPES = {0x0100000C: "arm64"}


def dump(path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 32:
        print(f"{path.name}: too short to be Mach-O")
        return
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _ = struct.unpack_from(
        "<IiiIIIII", data, 0)
    if magic not in (0xFEEDFACF, 0xFEEDFACE):
        print(f"{path.name}: not a thin Mach-O (magic {magic:#x}), skipping")
        return

    print(f"=== {path.name} ===")
    print(f"    size        {len(data)} bytes")
    print(f"    cputype     {CPU_TYPES.get(cputype, hex(cputype))} subtype {cpusubtype:#x}")
    print(f"    filetype    {filetype} ({FILE_TYPES.get(filetype, '?')})")
    print(f"    ncmds       {ncmds}   sizeofcmds {sizeofcmds}   flags {flags:#x}")

    off = 32
    present = set()
    segments = []
    for index in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        present.add(cmd)
        if cmd == 0x19:
            name = data[off + 8:off + 24].rstrip(b"\0").decode("ascii", "replace")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            segments.append((name, vmaddr, vmsize, filesize))
        if cmd == 0x1E:
            dataoff, datasize = struct.unpack_from("<II", data, off + 8)
            print(f"    LC_SEGMENT_SPLIT_INFO dataoff {dataoff} datasize {datasize}"
                  f"  (blob[0] {data[dataoff:dataoff + 1].hex() if dataoff < len(data) else 'n/a'})")
        off += cmdsize

    # The two answers this script exists to give.
    print(f"    LC_SEGMENT_SPLIT_INFO present: "
          f"{'YES' if 0x1E in present else 'NO'}")
    print(f"    __LINKINFO present:            "
          f"{'YES' if any(s[0] == '__LINKINFO' for s in segments) else 'NO'}")

    print("    segments:")
    for name, vmaddr, vmsize, filesize in segments:
        print(f"      {name:18} vmaddr {vmaddr:#018x} vmsize {vmsize:#010x} "
              f"filesize {filesize:#010x}")
    unknown = sorted(c for c in present if c not in NAMES)
    if unknown:
        print(f"    unknown load commands: {[hex(c) for c in unknown]}")
    print()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    for argument in sys.argv[1:]:
        path = Path(argument)
        if not path.is_file():
            print(f"{path}: not found", file=sys.stderr)
            continue
        try:
            dump(path)
        except Exception as exc:  # a malformed image should not hide the others
            print(f"{path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
