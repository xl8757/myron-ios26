#!/usr/bin/env python3
"""Give UniXNU's RELEASE reference build the host values a Mac supplies itself.

`scripts/build-fileset-release-reference.py` copies the prepared XNU tree and
runs make with a hard coded argument list. On a Linux runner that list is
incomplete: makedefs/MakeInc.cmd obtains the host CPU count and memory with
`sysctl hw.physicalcpu`, `sysctl hw.logicalcpu`, `sysctl hw.memsize` and
`sw_vers`, all of which are absent, so the values become empty and the build
stops before compiling anything:

    sysctl: cannot stat /proc/sys/hw/physicalcpu: No such file or directory
    make: sw_vers: No such file or directory
    /bin/sh: arithmetic expression: expecting primary: " * 4294967296"
    MakeInc.cmd:575: *** invalid second argument to 'wordlist' function: empty value.  Stop.

`scripts/build-xnu-4k-baseline.sh` already handles this — its Linux branch
passes MEMORY_SIZE and SYSCTL_HW_* as make assignments. Exporting them as
environment variables does not work, because MakeInc.cmd assigns those names
with `:=`, which overrides the environment; they have to arrive on the make
command line.

This script edits the copy of the reference script in the UniXNU checkout and
inserts exactly that. It is idempotent, and it fails loudly if the anchor it
edits is not there, so an upstream change surfaces as an error rather than a
silent no-op.

The other half of the Linux setup — PLATFORM, HOST_CC, MIGCOM and the source
rewrites from scripts/linux/prepare-xnu.py — is environment and tree state, not
make arguments. prepare-xnu.py has already run in the main kernel step and its
result is what .cache/xnu-work holds, so the copytree the reference script does
inherits it. The three variables are exported by the caller.
"""

from pathlib import Path
import argparse
import sys

ANCHOR = "    args = ['make','-C',str(source),'build','SDKROOT=iphoneos',\n"


# The link also stops on the LLVM profile runtime symbols that OSKext.cpp declares
# and expects compiler-rt to define. libcc_kext.a cannot be built on this runner
# (prepare-xnu.py libs needs the SDK headers that installhdrs produces, and the
# reference build does its own copytree without installhdrs), and for a
# comparison kernel that never runs these two symbols are irrelevant: stub them
# the same way the native hooks are stubbed. The pattern at the stub site only
# names native_*, boot_log_* and boot_cache_input_capture, so widen it.
STUB_OLD = (
    '    stub_names = sorted(set(re.findall(r\'^\\s+"(_(?:native_[a-z0-9_]+|'
    'boot_log_[a-z0-9_]+|boot_cache_input_capture))"\', build_log, re.M)))\n'
)
STUB_NEW = (
    '    stub_names = sorted(set(re.findall(r\'^\\s+"(_(?:native_[a-z0-9_]+|'
    'boot_log_[a-z0-9_]+|boot_cache_input_capture|__llvm_profile_[a-z0-9_]+))"\', build_log, re.M)))\n'
)

INSERT = """    # Linux hosts have no sysctl/sw_vers, and MakeInc.cmd assigns MEMORY_SIZE and
    # SYSCTL_HW_* with :=, which overrides the environment, so these have to be
    # make arguments. Same values scripts/build-xnu-4k-baseline.sh passes.
    host_args = []
    if os.uname().sysname == 'Linux':
        _pages = int(subprocess.check_output(['getconf', '_PHYS_PAGES'], text=True))
        _psize = int(subprocess.check_output(['getconf', 'PAGE_SIZE'], text=True))
        _cpus = str(os.cpu_count() or 4)
        host_args = [
            f'MEMORY_SIZE={_pages * _psize}',
            f'SYSCTL_HW_PHYSICALCPU={_cpus}',
            f'SYSCTL_HW_LOGICALCPU={_cpus}',
        ]
    args = ['make','-C',str(source),'build','SDKROOT=iphoneos', *host_args,
"""

MARKER = "# Linux hosts have no sysctl/sw_vers"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mu-root", type=Path, required=True,
                    help="the UniXNU checkout")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    target = args.mu_root / "scripts/build-fileset-release-reference.py"
    if not target.is_file():
        print("not found: %s" % target, file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")

    if MARKER in text and "__llvm_profile_[a-z0-9_]+))" in text:
        print("%s: already patched" % target.name)
        return 0

    if text.count(ANCHOR) != 1:
        print(
            "%s: expected exactly 1 make-argument anchor, found %d; the upstream "
            "script changed and this patch needs rewriting"
            % (target.name, text.count(ANCHOR)),
            file=sys.stderr,
        )
        return 1

    if args.check:
        print("%s: anchor found, would patch" % target.name)
        return 0

    text = text.replace(ANCHOR, INSERT, 1)

    if STUB_OLD in text:
        text = text.replace(STUB_OLD, STUB_NEW, 1)
        print("%s: __llvm_profile_* added to the stub pattern" % target.name)
    else:
        print(
            "%s: stub-pattern anchor not found; the profile symbols will be unresolved "
            "if the link needs them" % target.name,
            file=sys.stderr,
        )
        return 1

    target.write_text(text, encoding="utf-8")
    print("%s: Linux make arguments added to the reference build" % target.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
