# Bring-up log

## 2026-11-18: the payload cannot ride in the firmware volume either

The fixed-address route is exhausted and so is the embedded route. Both failures
are structural, and both are recorded here so the next attempt starts from the
measurements rather than from another guess.

### The address route: six probes, no payload anywhere

`fastboot boot` and a flashed `boot_a` with a normal restart were both tried.
Both behaved identically: `x0 = 0` at handoff, so no FDT, so no
`/chosen/linux,initrd-start`. The driver fell back to fixed addresses and every
one of them came up empty:

    0x01000000  faults - below DRAM_BASE 0x80000000, so it is MMIO
    0x81000000  never probed with a correct image (see below)
    0xA6F00000  Initrd_Small, empty
    0xF0000000  XNU_Payload, empty
    0xB8000000  DXE_Heap, empty
    0xA8000000  Kernel, empty
    0xF8800000  OEM_VM_REGION, empty

The `0x01000000` entry is worth keeping: it is the raw value of the boot image's
`ramdisk_addr` field, and reading it raises a synchronisation exception inside
this driver. `MemoryMapLib` does not declare it, and DRAM starts at 0x80000000,
so the read faults rather than returning data. An earlier note in
`make_payload.py` said "0x01000000 is not mapped on this platform" and moved the
address away - true, but it describes the UEFI page tables, not what the
bootloader did.

The stock images explain the confusion. `vendor_boot.img` carries
`kernel_addr = 0x8000` and `ramdisk_addr = 0x1000000`, neither of which is a DRAM
address, yet that image boots. So the bootloader adds `DRAM_BASE` itself and the
ramdisk would be at `0x80000000 + 0x1000000 = 0x81000000`. **That address was
never tested with a clean image** - the one build that carried it
(`79b3335`) also carried the vendor DXE set, and it stopped in `EnvDxe` before
the probe could run.

### The embedded route: FvMain needs more RAM than it has

`myron.fdf` puts the payload in `FvMain` as a FREEFORM RAW section, and
`FvMain` is LZMA-compressed into `FVMAIN_COMPACT`, which is what the FD holds.
The FD therefore only pays the compressed size - 11 MiB of payload with a
9.84 MiB kernel is about 2.78 MiB on flash - which is why the volume was grown
from 3 MiB to 5 MiB and the kernel embedded in it.

That reasoning stopped one step too early. **SEC has to decompress `FvMain` into
RAM before anything inside it can be reached**, and the decompressed size is the
uncompressed one:

| payload in FvMain | FvMain decompressed | FD | outcome |
| --- | --- | --- | --- |
| 1 MiB | about 3 MiB | 3 MiB | menu appears |
| 16 MiB | about 18 MiB | 5 MiB | black screen, falls back to Android |

The memory map has no 18 MiB window for it: `FV_Region` is 4 MiB,
`Sched_Heap` 4 MiB, `UEFI_RESV` 1.2 MiB. Decompression fails, no DXE driver
loads, and the phone black-screens. This is not a configuration error to tune -
`FvMain` cannot hold a kernel-sized payload on this board at all.

The FDF comment says the same thing from the other side: the payload section sits
in `FvMain` because `UniXNURamDiskDxe` looks it up with `GetSectionFromAnyFv`,
which can only find firmware volumes that already have the FV2 protocol
installed. `FVMAIN_COMPACT` is the SEC decompression layer and is not one of
them, so moving the payload there instead is not a drop-in change.

### Two more corrections worth keeping

The FD base moved from 0xA7100000 to 0xA6F00000 in one attempt, to absorb
`Initrd_Small`. That is where the bootloader puts the boot image ramdisk, and the
copied firmware landed on live data; the phone black-screened. The volume grows
upward from 0xA7100000 now, and `Initrd_Small` and `FD_Reserved` are back in the
map.

Three tool inputs hardcoded the old geometry and failed only after a successful
compile: the FD size assertion in the workflow, `check_artifacts.py`'s
`FD_BASE`/`FD_SIZE` constants, and `make_payload.py`'s `refd` size check. All
three read `Resources/Configs/myron.toml` now. The release notes do not record
`vendor`, so a published FD cannot be told apart from a `vendor=none` build by
its tag - another reason to read the setting from the config rather than copy a
number.

### What is built and verified, and what is not

Built and checked offline: the stripped 9.84 MiB MH_EXECUTE kernel is inside a
16 MiB FAT12 payload volume (`KERNEL.BIN`, Mach-O magic at offset 80384), and
that volume is inside a 5,242,976-byte FD whose base and size match
`myron.toml`. The CI chain builds it end to end. What is not verified is that
any of it reaches the kernel: the volume is in `FvMain`, and `FvMain` cannot be
decompressed here.

The device is back on Android, slot `a`, `/data` intact, `boot_a` stock and
`boot_b` holding a probe image. Nothing on the phone was modified in place.


## 2026-11-18: the kernel payload is never read, and the log says so exactly

Two boots with the corrected firmware (`vendor=none`, ramdisk address
0xF0000000, 28 MiB payload carrying the 16.6 MiB kernel) reached the UniXNU menu
and showed **one entry, `Hello payload`**. The log explains it:

    UNIXNURAMDISK: x0 0x0 is not a device tree
    UNIXNURAMDISK: nothing at 0xF0000000 either
    UNIXNURAMDISK: payload volume at 0xC23CE018, 1048576 bytes
    UNIXNURAMDISK: block device 0xC23CE018 size 0x100000 Success

So the bootloader placed no payload at 0xF0000000, and the driver fell back to
the 1 MiB volume baked into the firmware volume by `myron.fdf` - which contains
`unixnu.toml` and HELLO.EFI, no kernel. Everything above that is healthy: the
menu renders at 1200x2608, reads `\EFI\UniXNU\unixnu.tom` successfully, and
boots HELLO.EFI to `returned Success`.

The first attempt at this was my own mistake and is in the log above: I
dispatched the firmware build without `vendor=none`, so it stopped in `EnvDxe`
before any of this could be exercised.

### What the mechanism actually is

`docs/porting/uefi-firmware.md` states it: the Android bootloader places the FAT
payload in RAM and writes `/chosen/linux,initrd-start` and `linux,initrd-end`
into the firmware FDT. `Mu-Silicium` SEC preserves the incoming FDT address in
the entry-argument HOB, and `UniXNURamDiskDxe` reads it from there.

That path is closed on this board: `x0` is 0 at handoff, so there is no FDT, no
`/chosen`, and no initrd properties. The driver's fixed-address fallback exists
precisely because of this, and the fallback address is the only lever.

Whether the bootloader honours the header's `ramdisk_addr` at all is the open
question, and one address cannot answer it: a miss at 0xF0000000 means either
"it loaded the ramdisk somewhere else" or "it loaded no ramdisk".

### The probe, and a bug in it worth recording

`PublishFallback` now walks a list of addresses in declared DRAM and logs which
hold a FAT volume. The first version of that list was wrong:

| address | verdict |
| --- | --- |
| 0xD0000000 | **not mapped** - the read itself faults |
| 0xE0000000 | **not mapped** - same |
| 0x90000000 | inside `MPSS_REGION` - mapped, but modem firmware |

Checking the candidates against `MemoryMapLib` before building caught this.
Reading an unmapped address would have reset the platform before the diagnostic
could be read, which is the worst possible outcome for a probe: no information
and no evidence. The six that remain are all inside declared regions -
`Initrd_Small`, `XNU_Payload`, `DXE_Heap`, `Kernel`, `OEM_VM_REGION` and
`Display_Reserved`.

PENDING: the probe's output. A hit at an unexpected address means the ramdisk is
being loaded elsewhere and the driver only has to look there; no hits at all
means no ramdisk arrives and the payload has to come from somewhere else.


## 2026-11-18: the first kernel payload boot failed in firmware, on my own mistake

The image carried the 16.6 MiB MH_EXECUTE kernel inside a 28 MiB FAT volume loaded
at 0xF0000000, which is what the previous section set out to build. It never got
that far: the phone stopped in firmware with

    ASSERT_RETURN_ERROR (Status = Not Found)
    ASSERT AutoGen.c +652: !RETURN_ERROR (Status)
    _ock handle not initializedASSERT spinlock.c +108: Lock handle not initialized

That is `EnvDxe`, and it is the same failure this log already recorded when the
vendor DXE set is loaded on this board.

**Cause: I dispatched the firmware build without `vendor=none`.** The workflow's
default is `vendor=full`, which pulls in all 219 vendor inputs and 53 extra DXE
modules; the configuration that reaches the UniXNU menu is `vendor=none`, 52
modules. The dispatch I ran to pick up the 0xF0000000 changes passed only
`display=simple`, so it built the configuration known to stop in `EnvDxe`. The
address work was never exercised.

The lesson is not subtle but it is worth writing down: **the firmware dispatch
parameters are part of the configuration, and the default is the one that
crashes.** `vendor=none` has to be named explicitly on every dispatch that is
meant to reach the menu, or the build silently produces the crashing variant.
The build log shows no `vendor = none` line, which is the tell; a correct build
prints it.

Rebuilt with `display=simple vendor=none`, keeping the 0xF0000000 ramdisk
address, the driver's fallback base, the FAT12 cluster size of 16 sectors and the
8.3 `KERNEL.BIN` name. PENDING: boot that one.


## 2026-11-18: the 17.7.11 retarget ended in a reversal, and what it taught

Detail: `docs/RETARGET-17.7.11.md`. Short version here because the log is the
record of what was actually done.

The day moved the target to iPadOS 17.7.11, edited `board.toml` to match
(`os_profile`, `chip_id 8011`, the device patch series removed, `--allow-missing`
dropped), then moved it back. The edit was 5 substitutions; the revert is the
same 5 with the reasoning attached, and `board.toml` is semantically identical to
what it was before (verified by comparing parsed TOML, not text).

### Why it was attempted

The sibling port for this exact phone boots 17.7.11 to SpringBoard, on a
DEVELOPMENT kernel, without `--allow-missing`. 23F84's whole difficulty - the
RELEASE comparison image, the split-seg fix, the 41.2% pairing ceiling - does not
appear in that configuration at all.

### Why it was abandoned

Adopting it needs 67 files this repository does not have: 27 C/C++ sources under
`package/` (touch, GPU, display, input, keystore, command DB, PMGR, boot
progress) and 20 build scripts. The reference repository cannot be used as a
drop-in either - it is documentation, not a reproducible build:

    build-native-aes-bindings.py       named in its board.toml, absent from the tree
    build-native-virtio-bindings.py    same
    scripts/data/                      empty, so myron-observed.json is missing
    songyuanPkg/songyuan.dec           0 bytes
    XNU patch series                   not carried

`aogg2023/songyuan-uefi` does supply `songyuanPkg` in full (17 files), so the
firmware side was recoverable; the driver and script side was not.

### What the reading confirmed, and one correction

`scripts/make-myron-adt.py` is the generator that reached SpringBoard on this
phone, and its constants are exactly what `dts/myron-handoff.dts` already
carries: GIC distributor `0x17000000`, redistributor `0x17080000` size
`0x200000`, stride `0x40000`, timebase `19200000`, DRAM base `0x80000000`. Our
device tree was copied from it correctly.

Its `--cpu-regs` defaults to `'0'` - a single boot core - and the README says
secondary CPUs need a specific XNU patch and `sheng-smp=1`. **So a one-CPU tree
is the intended first-boot configuration.** That retracts an inference recorded
earlier in this log: the `EnvDxe` fault at `FAR 0x17422030` was read as a GICR
walk past the declared window (frame 29 at a 0x20000 stride). At the real stride
of 0x40000 it is frame 14, and one core is documented as correct, and the fault
is observed to track the display configuration rather than the GIC. The GIC
arithmetic was a coincidence.

### Net state

Nothing new reached the phone today. What changed in the repository: one new
document, one new finding in `porting.toml`, and a `board.toml` that says why it
did not move. The iOS 26 configuration still builds, still prelinks, and still
produces a candidate whose own manifest says `loadable: false`.


## 2026-11-18: decision - retarget to iPadOS 17.7.11

This is the point where the iOS 26.5.2 / 23F84 target stops being the plan.

### What forced it

Reading the sibling port for the same phone settled a question I had been
answering from the wrong end. `aogg2023/songyuan-device` is the Redmi K90 Pro Max
- same `kit = "myron"`, same `Platforms/Xiaomi/myronPkg`, same SM8850 - and its
README says it boots to SpringBoard, with touch, Adreno 840 Metal and a
persistent Data store. Two things about how it does that matter:

| | songyuan (boots) | myron-ios26 (ours) |
| --- | --- | --- |
| OS target | **iPadOS 17.7.11 (21H461)** | iOS 26.5.2 (23F84) |
| Identity | iPad7,1 / j120ap | iPad17,3 / j820ap |
| `[kernel.prelink].kernel` | DEVELOPMENT | RELEASE (changed by me this week) |
| `--allow-missing` | not used | used |
| prelink kit | NativePrelink | XnuPrelink - **the same repository** |

`NativePrelink` and `XnuPrelink` turned out to be identical: `emit_prelink.py`,
`fileset_match.py` and `kc_image.py` have the same sha256 in both. The repo was
renamed. So the tooling was never the difference - the OS target is.

### Why 23F84 was the wrong hill

UniXNU's support for 17.7.11 is complete and exercised: `profiles/os/ipados-17.7.11-21H461`
carries the kernelcache pin, the 31 roots, `early_security_init`, the trust cache
hashes, the payload `text_fixups`, and the whole ATD identity block. Its fixture
set has mature anchors.

23F84 has `profiles/os/ipados-26.5.2-23F84` too, but the 23F84 fixture is 108
functions. I did fetch it, and it did help - `stubbed functions` fell from 2018
to 1546 and `kext-facing targets named` rose by 86 - but it cannot close a
pairing ratio that is limited by something else:

    Apple kernelcache : 21113 functions, 2885 diversities, __TEXT_EXEC 44.9 MiB
    our RELEASE kernel: 25782 functions,  811 diversities, __TEXT_EXEC  7.3 MiB
    average function  : 2230 bytes vs 297 bytes

Our functions are 7.5x smaller on average. Apple's kernelcache is RELEASE-LTO, so
its functions are the result of link-time inlining; ours are not inlined and
there are more of them. Matching function *shapes* across that difference has a
ceiling, and no amount of anchors reaches it. LTO is the only thing that would
change the shapes, and this toolchain builds cctools-port with
`--disable-lto-support`.

But the decisive point is not that LTO is hard. It is that **the port that boots
does not use RELEASE or LTO at all.** It uses a DEVELOPMENT kernel, no
`--allow-missing`, and a complete 17.7.11 profile. So chasing LTO would have been
solving a problem the working configuration does not have.

### What this costs, honestly

- The kernel source changes from xnu-12377 to xnu-10063 - a different tree, and
  the arm64e patch series is per-version.
- `patches/xnu26/sheng-platform-sleep.patch` and the other iOS 26 device patches
  do not apply to 17.7.11.
- The iPad7,1 IPSW has to be fetched (~10 GB, ZIP64 with a usable Range
  endpoint, so only the KernelCache member is needed).
- The `--allow-missing` prelink work and the split-seg fix were for the RELEASE
  path; the DEVELOPMENT path is what the working port uses.

What carries over unchanged: the UEFI platform package (`myronPkg` boots to its
boot manager and has been verified on the phone), the component set, and every
firmware observation recorded in this log.

The iOS 26 work is not deleted. It is parked in this log and in `porting.toml`,
because if UniXNU's 23F84 support matures the same board file can point back at
it.


## 2026-11-18: split-seg info restored, prelink emits, pairing reaches 41.2%

The blocker from the previous section is fixed. `-Wl,-add_split_seg_info` now
reaches the RELEASE link and `emit_prelink.py` writes a candidate image.

### What was actually wrong

Comparing the two kernels' load commands settled it:

| | DEVELOPMENT | RELEASE (before) | RELEASE (now) |
| --- | --- | --- | --- |
| ncmds | 27 | 25 | 27 |
| LC_SEGMENT_SPLIT_INFO | yes, 1,474,672 B | **no** | yes, 842,880 B |
| __LINKINFO | yes | yes | yes |

Both carry `__LINKINFO`, which `LDFLAGS_KERNEL_ONLY` creates from the same
variable that holds `-Wl,-add_split_seg_info`:

    LDFLAGS_KERNEL_ONLY_SUBARCH_arm64e = -Wl,-add_split_seg_info -Wl,-kernel

So the option was being asked for and then lost on the RELEASE link, not
forgotten. Rather than keep hunting through ld64 for the place it is dropped,
`patch-release-reference-linux.py` now appends a command-line make assignment:

    LDFLAGS_KERNEL_ONLY_SUBARCH_arm64e=-Wl,-add_split_seg_info -Wl,-kernel

A command-line variable outranks the makefile's own definition, so this restores
it for every link rule that expands `LDFLAGS_KERNEL_ONLY`. `-Wl,-kernel` comes
along because overriding replaces the variable's whole value.

The first attempt at this put the bare `-Wl` option into `host_args`, which is
spliced into `make`'s argv - there it would have been read as a build goal. That
is recorded because it is the kind of mistake this file keeps having to correct.

### Results

`diversities` went from **0 to 811**. That is the number that was wrong all
along, and split-seg data is where it comes from: the split-segment relocation
stream is what the matcher reads signatures out of.

| path | before | after |
| --- | --- | --- |
| default | 6289 / 21113 (29.8%) | **7646 / 21113 (36.2%)** |
| reference | 7726 / 21113 (36.6%) | **8705 / 21113 (41.2%)** |
| kext-facing named | 972 / 3094 | **1316 / 3094** |

For the record, the DEVELOPMENT image pairs 6805 (32.0%), so the RELEASE
configuration plus a working split-seg stream is a clear improvement over where
this started.

### The image is emitted, and it says it is not loadable

    kernel.macho         86,081,536 bytes (82.1 MiB)
    candidate_sha256     bb31e063cdc7dbe0a78f8b8fbe7be5b4b5d9ea0b8a03967c90d14299a6681789
    format               MH_FILESET
    mapping_complete     False
    runtime_qualified    False
    loadable             False
    missing_address_space  output collection

    unresolved closure references: 639; stubbed functions: 2018

Rebinding statistics that are all real work, not estimates: 42,024 branches,
47,168 kext pointers, 82,701 kext chain sites kept, and the native split
relocations consumed were 44,237 pointers plus 99,296 kind_5 and 106,457 kind_6
records.

Six hundred thirty-nine unresolved closure references across 27 kexts (apfs has
the most), and `loadable: false` in the emitter's own manifest. So the honest
statement is: the pipeline runs end to end and produces a candidate, and that
candidate is not claimed to boot. `board.toml`'s warning still stands.

### The size problem is back, differently

The RELEASE kernel alone is 15.8 MiB and fit the 56 MiB `XNU_Payload` window.
The prelinked fileset is 82.1 MiB, because it has to carry the rebound kexts.
So the window question returns, one layer up: it is not the kernel that has to
fit, it is the collection.


## 2026-11-18: the pairing ratio, measured against the real kernelcache

First real pairing numbers. Everything before this section reported 32% from the
DEVELOPMENT image; this is the RELEASE comparison kernel against the actual
23F84 kernelcache.

    Apple kernelcache  : 21113 functions, 2885 diversities
    our RELEASE kernel : 25782 functions,    0 diversities

Both pairing paths ran:

| path | paired | ratio | what carried it |
| --- | --- | --- | --- |
| default (hash + strings) | 6289 / 21113 | **29.8%** | 3595 string anchors, 376 PAC-shaped, 2111 calls, 16 data-users |
| reference (RELEASE as comparison) | 7726 / 21113 | **36.6%** | 5147 order anchors, 373 symbol anchors, 1364 strings, 668 calls, 111 pure leaves |

Against DEVELOPMENT for the same kernelcache:

    DEVELOPMENT: 29411 functions, 6805 paired = 32.0%
    RELEASE    : 25782 functions, 7726 paired = 36.6%
    delta      : +921 functions, +4.6 points

The more telling number is `kext-facing targets named`: 490/3094 on the default
path, 972/3094 on the reference path. That doubled, and it is the measure that
matters for binding kexts, not the raw function ratio.

### Getting the kernelcache right was most of the work

The file already on disk (`kernelcache.release.ipad17`, sha256 `ff7b0996...`) was
the IM4P container, not the kernelcache. `profile.toml` pins the *decompressed*
arm64 slice (`0f7d50ac...`), which is why the hashes never matched. The chain is:

    IMG4 SEQUENCE  ->  OCTET STRING payload  ->  LZFSE ("bvx2")  ->  Mach-O

Decompressing it with `liblzfse` (the import name is `liblzfse`, not the package
name `pyliblzfse`) gives 74,219,520 bytes with sha256 `0f7d50ac1bfc...`, exactly
the profile value. That image is `filetype 12` MH_FILESET with `arm64e`
cpusubtype and 311 load commands.

Finding it in the IPSW without downloading 10.4 GB: the file supports HTTP Range
(206) even though HEAD reports no `Accept-Ranges`, and the archive is ZIP64, so
the central directory lives at `cd_off = 0xFFFFFFFF` in the classic EOCD and has
to be read from the ZIP64 locator. With that, `kernelcache.release.ipad17` sits
at local header offset 10,422,143,265, 22,569,956 compressed.

### It cannot emit an image yet

`emit_images` stops with:

    ValueError: Native image has no split-segment relocation information

`fileset_layout.split_references` reads load command 0x1e (`LC_SEGMENT_SPLIT_INFO`)
and **neither binary has it**: not our RELEASE kernel (25 load commands,
`LC_SYMTAB` only) and not Apple's kernelcache (311 commands, `LC_DYLD_EXPORTS_TRIE`
and no symtab). So pairing is computable but the emitted image is not.

### What the numbers say about the real problem

`diversities = 0` on our side against 2885 on Apple's. Diversities come from the
compiler's function-signature hashing, and a build that produces none is not
producing comparable signatures at all. That is a different and deeper problem
than anchor coverage: more reviewed anchors cannot fix it, because the shapes
being compared were computed by a different scheme. Raising the ratio further
means making our clang emit the same signature scheme, which is a toolchain
question rather than a pairing one.

This also keeps `board.toml`'s warning honest: "Expect a boot that fails at the
first rebound kext rather than a working system." 36.6% with zero diversities
does not contradict it.

### Where the RELEASE work still paid off

Independently of pairing, the RELEASE kernel is 15.8 MiB (MH_EXECUTE,
`arm64e`, 172,141 symbols) against DEVELOPMENT's 23.3 MiB. The 56 MiB
`XNU_Payload` window was blocking any boot test; that block is gone.


## 2026-11-18: the RELEASE comparison kernel builds

`scripts/build-fileset-release-reference.py` now runs to completion on the Linux
runner and produces `mach.release.bcm2837.unstripped.noctf`. This is the
configuration the prelink pairing needs: `KERNEL_CONFIGS=RELEASE` instead of the
`DEVELOPMENT` that `build-xnu-4k-baseline.sh` hard codes.

Measured locally from the artifact:

    size        16,572,552 bytes (15.8 MiB)
    magic       0xfeedfacf  MH_MAGIC_64
    cputype     0x0100000c  CPU_TYPE_ARM64
    filetype    2           MH_EXECUTE      <- what DarwinBoot's parser accepts
    ncmds       25
    symbols     172,141 total, 97,394 defined, 6,676 undefined

For comparison the DEVELOPMENT kernel is 24,392,328 bytes (23.3 MiB), so RELEASE
is 35% smaller, which is what turning off the debug and assert paths should do.
Both are well under the 56 MiB `XNU_Payload` window, so the size problem that
blocked a boot test is gone for either configuration.

### What it took, in order

Every one of these was a separate run, and each was a real defect rather than a
guess:

1. **No Linux branch in the reference script.** It copies the tree and runs make
   with a hard coded argument list, so make ran `sysctl hw.physicalcpu`,
   `sw_vers` and `hw.memsize`, got nothing, and died in `MakeInc.cmd:575`
   (`invalid second argument to 'wordlist'`). MakeInc assigns `MEMORY_SIZE` and
   `SYSCTL_HW_*` with `:=`, which overrides the environment, so the values have to
   be make arguments. `scripts/patch-release-reference-linux.py` inserts the same
   assignments `build-xnu-4k-baseline.sh` passes.
2. **Toolchain paths.** `xnu-host-cc` is at `<prefix>/CommandLineTools/usr/bin`,
   not `<prefix>/bin` (setup-toolchain.py line 371).
3. **`flex: fatal internal error, exec of failed`.** The shims live in
   `CommandLineTools/usr/bin`; with only `<prefix>/bin` on PATH flex could not
   find `gm4`.
4. **`clang: line 32: /opt/unixnu-linux/llvm/bin/clang: No such file`.** The clang
   wrapper execs `${CROSS_CLANG:-/opt/unixnu-linux/llvm/bin/clang}` and the
   default does not exist here. The reference script bypasses the `unixnu` driver,
   so it never got the twelve variables `scripts/unixnu/kernel.py` lines 102-109
   set for the normal kernel step. They are copied into the step.
5. **`Undefined symbols ... _arm64_platform_sleep_hook`** (this one broke the
   *main* kernel build, not the reference build). `ArmCpu.cpp` declares the hook
   `weak_import`, but an authenticated pointer to an undefined weak symbol does
   not link on arm64e. The definition is in `sheng-platform-sleep.patch`, which
   UniXNU-sheng carries as its last device-series entry. Only the `osfmk/arm64/cpu.c`
   hunks are used: the full patch also touches `cpu_common.c` and
   `IOPMrootDomain.cpp`, and its `cpu_common.c` hunk no longer applies because the
   arm64e series already rewrote `sheng_sigp_recover`.
6. **LTO cannot work here.** `setup-toolchain.py` builds cctools-port with
   `--disable-lto-support` ("the kernel link uses no LTO"), so our ld64 has no
   LLVM LTO backend. `BUILD_LTO=1` makes clang emit bitcode and ld ignored all 956
   objects as `unknown-unsupported file format`, then failed. Dropping `--lto`
   keeps `KERNEL_CONFIGS=RELEASE`, which is the part that matters for pairing, and
   links ordinary Mach-O objects.
7. **`Undefined symbols ... __llvm_profile_*`.** `OSKext.cpp` declares
   `__llvm_profile_get_size_for_buffer_internal` and
   `__llvm_profile_write_buffer_internal` and expects compiler-rt to define them.
   `prepare-xnu.py libs` builds those into `libcc_kext.a`, but it cannot run on
   this runner: it needs the SDK headers `installhdrs` writes into the fakeroot
   and the reference build does its own copytree without `installhdrs`, so it
   fails with `sys/_types.h file not found`. The reference script already stubs
   undefined `native_*` hooks for exactly the same reason (this comparison kernel
   never runs), so its stub pattern was widened to cover `__llvm_profile_*`.

### The measurement bug worth remembering

The first attempt at counting functions printed `0 text symbols` for both
kernels. The cause was `nm -arch arm64e`: `-arch` is cctools' flag, and the `nm`
on PATH is GNU nm, which rejects it and prints nothing. Parsing the Mach-O
`LC_SYMTAB` directly is the reliable way to count here.

PENDING: the pairing ratio itself. The RELEASE kernel exists, but prelink has not
been run against it, so the 32% figure from the DEVELOPMENT image still stands.
Measuring it needs the 23F84 kernelcache, which CI cannot hold.


## 2026-11-18: adding drivers back has to follow the dependency chain

Baseline is 52 modules (`vendor=none`). Adding drivers back one group at a time
only works if a group carries everything its members need, and the first two
attempts did not.

### Attempt 1: ButtonsDxe + ChipInfo + ClockDxe - halted at ChipInfo

    Loading driver at 0x000C28E3000 EntryPoint=0x000C28E4000 ChipInfo.efi
    ASSERT AutoGen.c +571: !RETURN_ERROR (Status)
    ASSERT DALModEnv.c +115: !EFI_ERROR (Status)
    ASSERT DALModEnv.c +115: gpDALSYSLInitFnPtr

My reading of this was wrong at first. I assumed the assert patch had missed
`ChipInfo` and looked for another way to neutralise asserts. That was a dead end:
Mu's `DebugAssert` is

    AsciiSPrint(...); SerialPortWrite(...);
    if (mask & DEBUG_PROPERTY_ASSERT_BREAKPOINT_ENABLED) CpuBreakpoint();
    else if (mask & DEBUG_PROPERTY_ASSERT_DEADLOOP_ENABLED) CpuDeadLoop();

and this board's `PcdDebugPropertyMask` is 0x0F, which contains neither bit, so
an ASSERT prints and returns. **ASSERTs were never what halted the boot.** The
real stop was the null `gpDALSYSLInitFnPtr` call after it, i.e. `ChipInfo`
running without `DALSys`.

### Attempt 2: added DALSys and PlatformInfoDxeDriver - halted inside DALSys

    Loading driver at 0x000C28B3000 EntryPoint=0x000C28B4000 DALSys.efi
    ASSERT AutoGen.c +382: !RETURN_ERROR (Status)
    Unable to locate smem protocol! status=0xE
    Unable to locate shared memory! status=0xE

0xE is EFI_NOT_FOUND. `DALSys` needs the SMEM protocol, which `SmemDxe`
installs - in the earlier GUID survey `15ffbfaa-ca8c-4ce3-bcea-dabe705e664d` was
referenced by `SmemDxe` and nothing else. So the chain is longer than one level.

### The dependency chain, as measured

    EnvDxeEnhanced  publishes the device tree HOB
      -> SmemDxe    needs the tree, installs the SMEM protocol
        -> DALSys   needs SMEM
          -> ChipInfo, PlatformInfoDxeDriver
            -> ClockDxe, DDRInfoDxe

Each level fails with a named protocol in the log, which is what makes this
walkable: the message says which link is missing.

### Attempt 3: the whole chain at once

`EnvDxeEnhanced SmemDxe DALSys PlatformInfoDxeDriver ChipInfo ClockDxe
DDRInfoDxe ButtonsDxe`.

### The skip mechanism, and what it does not do

`tools/patch-exception-skip.py` rewrites Mu's AArch64 handler from

    LogToConsole (UnicodeBuffer); ASSERT (FALSE); CpuDeadLoop ();

to printing `SKIP ON FAULT: ELR 0x... -> 0x...` and advancing ELR past the
faulting instruction. It is applied in CI and the string is confirmed present in
the built `DxeCore`.

It did not help attempts 1 and 2, and that is informative rather than a failure:
those boots stopped on *returned error statuses*, not on CPU exceptions. The
patch only fires when a driver touches dead memory, so a missing dependency still
halts the boot. The two mechanisms cover different failures:

| failure | handled by |
| --- | --- |
| vendor ASSERT | `patch_vendor_asserts.py` (nops the spin inside the assert printer) |
| CPU exception on dead MMIO | `patch-exception-skip.py` (advances ELR) |
| driver entry returns an error | DxeCore itself: prints `FAILED` and continues |
| **missing protocol dependency** | **nothing - the driver halts** |

The last row is what attempts 1 and 2 hit. There is no generic fix: a driver that
cannot find its protocol will not run correctly whatever we do, so the group has
to be complete.

## 2026-11-17 late: baseline reproduced on hardware

The 52-module firmware (`fd-20261117-151756`, `vendor=none`, `display=simple`)
boots on the phone and exports its log. Diffed against the earlier known-good
capture (`myron-20190100-000721.log`) line by line with timestamps stripped:

    155 lines each, 151 identical
    4 differences, all heap/logger addresses:
      VarCheckLib handler   0xC2851958 -> 0xC2871958
      Advanced Logger base  0xC2C57000 -> 0xC2C6B000
      payload volume        0xC23AF018 -> 0xC23CE018

Those shifts are the expected consequence of one fewer module (`ButtonsDxe`),
not a behavioural difference. The chain is identical:

    [Bds] Entry -> GraphicsConsole 1200x2608 -> Booting UniXNU
    -> UniXNU 0.1.0 -> config loaded -> menu shown -> 10 s timeout
    -> HELLO.EFI returned Success -> AUTOLOG QR viewer

Saved as `work/logs/myron-20190100-000120.log`.

### Honest note on how this baseline was obtained

Everything from `d6d5ec4` to HEAD that touched the platform package was reverted
in one go, not item by item. That got a working reference quickly but throws
away the information about which of my changes mattered. Two are known
necessary: `make_payload.py` needs its `repack` subcommand, and the vendor
manifest must match the extractor's pin or the run stops at
`vendor input mismatch: DxeCore`.

### The input problem, for tomorrow

Both logs report

    SetupKeypad: Failed to Locate STI Device Path! Status = Not Found
    Connect ConIn failed - Not Found!!!

There is no input device at all; the menu boots by its 10 s timeout. So adding
drivers has a trap: if a new driver changes timing or menu behaviour, the boot
can stall with no way to interact. That makes input-capable drivers the first
group to add, not an afterthought.

### Plan for adding vendor drivers back

Each group is one dispatch + one boot, and any group that faults gets bisected
inside itself.

| group | modules | purpose | risk |
| --- | --- | --- | --- |
| 1 | ButtonsDxe | restore input, so a stalled menu is recoverable | low |
| 2 | ChipInfo, DDRInfoDxe, ClockDxe | SMBIOS/DDR/clock query protocols | low |
| 3 | EnvDxeEnhanced, SmemDxe | device tree and shared memory | high - these were the crash source |
| 4 | UFSDxe, SPMI, ICBDxe | storage access | high |

`tools/experiment.py --vendor none --keep NAME` is the mechanism: `drop_vendor`
comments every `Binaries/` line as `#NOVENDOR INF ...`, and `keep_vendor` finds
those by name and uncomments them, so no hand editing is needed.

Also worth remembering: the baseline has no storage driver (`Failed to Find UFS
IORT Node!` every boot), so it can only run payloads from the FAT volume. A
kernel needs group 4 plus the platform drivers from group 3.

## 2026-11-17: why the known-good build boots - it has no vendor DXE at all

Chasing the 53-vs-106 module gap found it, and it is not a driver-version issue.

`tools/experiment.py` takes a `--vendor` switch. The workflow default is
`full`, but the build that reaches the UniXNU menu reports

    display = simple    1
    vendor  = none     112

so it was dispatched by hand with `vendor=none`, which comments out 112 vendor
entries from `DXE.inc` / `APRIORI.inc`. That is the whole difference. The
known-good firmware does not contain `EnvDxe`, `SmemDxe`, `ChipInfo`, `UFSDxe`,
`ClockDxe` or any of the rest; it is the core DXE stack, `SimpleFbDxe`,
`DtbExtensionHobDxe`, UniXNU and the payload machinery.

Reproduced with `-f display=simple -f vendor=none`:

| | known-good | rebuilt |
| --- | --- | --- |
| modules | 53 | **52** |
| module sets | - | identical except `ButtonsDxe` is missing |
| myron-boot.img | 2404352 | 2396160 |
| vendor inputs | 219 (OS3.0.13.0) | 219 |

`ButtonsDxe` is the one difference and it never did anything in the working
build either: its log line is `SetupKeypad: Failed to Locate STI Device Path`,
so key input was already dead and the menu relied on its 10 s timeout.

### The consequence for reaching a kernel

The configuration that boots has no storage driver, so it cannot read a
partition and cannot boot anything but a payload from the FAT volume. Getting to
a kernel is therefore not "add the kernel to this firmware" - the payload can
carry it, but the kernel needs the storage and platform drivers that were
switched off, and those are exactly the ones that fault.

So the two tracks are now clearly separate:

1. **Firmware track**: `vendor=none` boots to the UniXNU menu. Extending it means
   adding vendor drivers back one group at a time and testing.
2. **Kernel track**: `kernel/kernel.macho` is built (89423872 bytes, sha256
   68bcd2fb...). Booting it needs a payload that carries it plus whatever the
   prelink and Data seed steps produce, and those need the 23F84 IPSW and the
   device's unique chip id, neither of which can live in the repository.

### Reverts made to get here

Everything from `d6d5ec4` to HEAD that touched the platform package was reverted:
`DXE.inc`, `APRIORI.inc`, `RAW.inc`, `myron.dsc`, `myron.fdf`,
`DtbExtensionHobDxe.{c,inf}`, `AutoLogDxe.{c,inf}`, `UniXNURamDiskDxe.c`,
`tools/make_payload.py`, `fetch_vendor_inputs.py` and
`Resources/vendor-inputs.json`. Two of those mattered for the build to even
succeed: `make_payload.py` needs its `repack` subcommand, and the vendor
manifest has to match the firmware the extractor pins (OS3.0.13.0, 219 entries,
`DxeCore` sha `b6c8d8c9...`) or the extractor stops at
`vendor input mismatch: DxeCore`.

## 2026-11-17: a known-good boot, captured from the phone

The release `fd-20261112-065918-d6d5ec4-refd` (source `d6d5ec4efc02`, Mu
`46e4ac08`, `display=simple`) boots all the way through, and the phone exported
its log through the on-screen QR viewer. Saved at
`work/logs/myron-20190100-000721.log`.

The whole chain works:

    00:06:57.612  [Bds] Entry...
    00:06:57.819  GraphicsConsole video resolution 1200 x 2608
    00:06:57.865  FrameBufferBase: 0xFC800000  PixelsPerScanLine: 0x4B0 (1200)
    00:07:00.869  [Bds]Booting UniXNU
    00:07:00.929  UNIXNU: UniXNU 0.1.0 starting
    00:07:00.930  config: loaded \EFI\UniXNU\unixnu.tom
    00:07:00.986  menu: shown 1 entries, default 0 'hello'
    00:07:10.985  boot: entry 'hello' (Hello payload)          <- 10 s menu timeout
    00:07:11.045  boot: starting \PAYLOADS\HELLO.EFI
    00:07:11.047  boot: \PAYLOADS\HELLO.EFI returned Success
    00:07:21.026  AUTOLOG: starting official QR log viewer (1200x1200)

So UEFI -> BDS -> UniXNU menu -> payload handoff all work on this phone. The
firmware is not fundamentally broken.

### What that build contains

53 modules. It carries only the core DXE stack plus `SimpleFbDxe` and a custom
`DtbExtensionHobDxe`; it does **not** carry the 46 vendor drivers (`EnvDxe`,
`SmemDxe`, `ChipInfo`, `UFSDxe`, `ClockDxe`, ...) nor `DisplayDxe`. Those are
exactly the modules that, in the current tree, produce the fault.

### The hardware errors it logs, and survives

    RamManagerDxe  ASSERT Status = Not Found            (no RAM partition protocol)
    Failed to Locate Clock Protocol / DDR Info / Chip Info
    Failed to Find UFS IORT Node!
    SetupKeypad: Failed to Locate STI Device Path      (no key input)
    DTBHOB: no entry argument HOB, ABL passed no tree  (device tree handoff failed)
    UNIXNURAMDISK: x0 0x0 is not a device tree

These are the same failures chased for several rounds in this log. In this build
they are logged and the boot continues, because the drivers that would consume
those protocols are absent. That is the difference between the two trees: not
that the protocol lookups succeed, but that nothing depends on them.

Note also `GraphicsConsole video resolution 1200 x 2608` and
`PixelsPerScanLine 0x4B0` = 1200, so the panel really is 1200x2608 with a
1200-pixel scanline. That confirms the geometry measured on Android and means
the `PcdFrameBuffer*` values are not the panel's own numbers.

### What this changes

The question is no longer "why does the firmware not boot" - it does, at
`d6d5ec4`. It is "which change between `d6d5ec4` and the current HEAD breaks
it". Every modification in this session after that commit is a candidate, and
the honest way forward is to bisect against this known-good baseline rather than
keep changing several things at once.


Reverse chronological. Every entry says what was observed, on what evidence, and
what it means for the next attempt. Hardware observations are separated from
build observations on purpose: a green build proves nothing about the phone.

## 2026-11-17: the vendor ABI was the problem, not the device tree

### The EnvDxe abort was the display configuration

`USE_CUSTOM_DISPLAY_DRIVER` was 0, so the build used `SimpleFbDxe` and the DXE
stack stopped on

    Synchronous Exception at 0x000C2BE6EC
    FAR 0x000000017422030
    ESR : EC 0x25  IL 0x1  ISS 0x00000010
    Data abort: Synchronous external abort

Computing it against the captured tree:

    myron.dtb  GICD  0x17000000 + 0x10000
               GICR  0x17080000 + 0x200000      -> ends at 0x17280000
               ITS   0x17040000 + 0x20000
    fault     0x17422030 = GICR frame 29 + 0x2030

Frame 29 is far past the sixteen frames the tree describes, and `myron.dtb` and
`canoe.dtb` declare identical GIC geometry, so the tree was not the difference.
Setting `USE_CUSTOM_DISPLAY_DRIVER = 1` removed the abort outright and the panel
initialised, which is the evidence that matters: it was the display path, not
GIC.

### Why it took five builds to try that

`tools/experiment.py` rewrites the flag from the `--display` input on every
build:

    lines[i] = re.sub(r"=.*", "= " + ("1" if mode == "custom" else "0"), line)

The workflow default is `display=simple`, so editing the DSC to 1 was silently
undone each time. The build log printed `dsc: USE_CUSTOM_DISPLAY_DRIVER = 0`
while the repository said 1, which is what finally made it visible. A dispatched
run with `-f display=custom` is the only way to build with the vendor display
driver until that default changes.

### The streaks are a display mode mismatch, and the PCDs are not the panel size

Two builds differing only in `myron.dsc` (8 lines, the two `PcdFrameBuffer*`
values) behave completely differently:

| framebuffer PCDs | result |
| --- | --- |
| 1264x2780 | panel initialises, console draws as horizontal streaks, **no fault** |
| 1200x2608 | **synchronous external abort at 0x17422030** inside EnvDxe, before the panel is up |

1200x2608 with a 4800-byte stride is the panel's real geometry (SurfaceFlinger);
FINDINGS.md records the firmware GOP as 1220x2656. Neither makes DisplayDxe
happy. 1264x2780 is what `songyuanPkg` sets, and it is the value that gets the
stack running.

So the PCDs are an input the vendor driver expects in *its* units, not a
description of the panel. The streaks are the mismatch being presented instead
of faulting, which is strictly better: the DXE stack runs.

The remaining work is to give DisplayDxe this panel's real mode. Its INF ships
`PE32|DisplayDxe.patched.efi`, and Device-Binaries carries a `.patched` variant
per device (`b4q`, `b5q`, `nuwa`, `pong`, `vili`, `vayu`, `nabu`, `gts8p`), so
patching the binary for a given panel is the established mechanism rather than
something to invent. aston's patched binary targets the OnePlus 15 panel.

### Correction to an earlier claim in this log

I wrote that `EnvDxe` was walking GICR frame 29 and that the device tree was
"not the difference". The frame-29 arithmetic is right, but the conclusion is
backwards: the GIC geometry in `myron.dtb` and `canoe.dtb` is identical, and the
fault is switched on and off by the framebuffer PCDs, so it is the display
initialisation path that reaches that address, not a GIC walk.

### Current state: streaks, not a dead panel

With the vendor DisplayDxe in and songyuanPkg's geometry (1264x2780) the console
draws as horizontal streaks across the whole panel. That is a stride or size
mismatch: the panel is initialised and being written to. This phone's measured
value is 1200x2608 with a 4800-byte stride (SurfaceFlinger), so the geometry is
back to that; the next boot tests it.

### Build result of the aston switch

Green. The firmware now contains what the working sibling contains:

    modules            98   (was 106)
    EnvDxe             present
    EnvDxeEnhanced     absent
    SmemDxe            present
    DtbExtensionHobDxe absent
    load order         DxeCore -> PcdDxe -> EnvDxe -> ReportStatusCodeRouter ...
    myron-boot.img     1847296 bytes   (was 2027520)
    SILICIUM_UEFI.fd   sha256 e7fbecef859e...

Not yet booted on hardware. The build being green proves the configuration
resolves and the vendor set links; it says nothing about whether the DXE
assertion is gone. That is the next hardware check.

### Three build defects introduced and then fixed during the switch

1. **Missing `Binaries/` prefix.** The path rewrite produced a mix: some entries
   were `Binaries/aston/QcomPkg/...` and some were `aston/QcomPkg/...`. EDK2
   resolves the former against WORKSPACE and stops on the latter with
   `aston/QcomPkg/Drivers/SmemDxe/SmemDxe.inf is not found in packages path`.
   Adding `Binaries` to `PACKAGES_PATH` did **not** help, which is the clue that
   the prefix itself was the problem, not the search path. All 49 vendor INF
   lines now carry the prefix.
2. **`patch_vendor_asserts.py` failed the build.** It reported
   `0 call(s) neutralised across 0 module(s)` and exited non-zero under
   `--require 1`. The aston set has no assert-printer deadloops; that patching
   was only ever needed for the drivers extracted from the phone, which had
   Xiaomi's DEBUG assert printer compiled in. The step now runs with
   `--require 0 || true` so a future change to the set is still visible in the
   log.
3. **The memory map validator failed first.** It was left pointing at the BSP
   names from the rename that has since been reverted. Restoring the validator
   with the live-FDT names fixed it: `PASS: 77 descriptors, 63 AddMem ranges,
   no overlaps`.


### What the sibling package does

`aogg2023/songyuan-device` is a UniXNU device repository for **the same phone**
(Redmi K90 Pro Max, SM8850, codename `songyuan`). Its README states the port
boots iPadOS 17.7.11 to SpringBoard, with touch, Metal and a persistent Data
store, entirely through `fastboot boot`. Its UEFI package is
`aogg2023/songyuan-uefi` -> `Platforms/Xiaomi/songyuanPkg`.

Its platform package differs from `myronPkg` in four ways that matter:

| | songyuanPkg (boots) | myronPkg (asserted) |
| --- | --- | --- |
| vendor binaries | `Binaries/aston` (Mu submodule, OnePlus 15) | `Binaries/myron` (extracted from the phone) |
| EnvDxe | `EnvDxe.inf` | `EnvDxeEnhanced.inf` |
| device tree | `DtPlatformDxe` and the `DeviceTreeBlob` FREEFORM are **commented out** | both present |
| raw files | `Binaries/aston/RawFiles/*` | `Binaries/myron/RawFiles/*` |

The driver path convention is `Binaries/aston/QcomPkg/Drivers/<Name>Dxe/<Name>Dxe.inf`,
not the flat `Binaries/myron/<Name>/<Name>.inf` the extractor produced.

### Why that explains the assertions

`Binaries` is a git submodule of `Project-Silicium/Mu-Silicium` pointing at
`Project-Silicium/Device-Binaries`, which carries 77 device directories
(`aston`, `dodge`, `annibale`, ...). Mu-Silicium builds against them directly;
`songyuan-uefi`'s CI does not fetch or extract anything.

The extractor in this repository instead pulled the phone's own DXE drivers out
of `images/uefi.img` in the HyperOS 4.0.6.0 package. Those are the drivers the
phone's **stock firmware** runs, which is a different build generation from the
aston set Mu is developed against. Both sets produce `EnvDxeEnhanced.efi` at
77824 bytes, but the SHA-256 differs, and the extracted one returns
`EFI_NOT_FOUND` from its entry point on this platform.

### Reverted, with the evidence

- **Memory map rename reverted.** I had renamed `XBL_DTLOG_REGION` -> `XBL_DT`,
  `SMEM_REGION` -> `SMEM`, `AOP_IMAGE_REGION` -> `AOP`, `PDP_NS_SHARED_REGION`
  -> `PDP_NS`, `PVM_FW_REGION` -> `PvmFw`, on the theory that the vendor drivers
  look up BSP names. `songyuanPkg` keeps the **original** `XBL_DTLOG_REGION`
  names and boots, so the rename was wrong. `docs/DXE-REGION-NAMES.md` keeps the
  comparison but its conclusion is superseded by this entry.
- **`DtbExtensionHobDxe` removed** from `myron.fdf`, `myron.dsc` and
  `APRIORI.inc`, along with the `DeviceTreeBlob` FREEFORM and `DtPlatformDxe`.
- **CI now skips vendor extraction** and builds against `Binaries/aston`. The
  extractor, its pinned images and `vendor-inputs.json` stay in the repository
  unused, so the comparison is reproducible.
- `patch_vendor_asserts.py` was repointed from `Binaries/myron` to
  `Binaries/aston` with `--require 1`, because the old `--require 200` count was
  tuned for the extracted set.

### Verified facts from this round

- The firmware image really did carry the rename: `XBL_DT` and `SMEM` present,
  `XBL_DTLOG_REGION` and `SMEM_REGION` absent. The assertions were unchanged, so
  the rename was not load-bearing either way.
- Memory map validator: `PASS: 77 descriptors, 63 AddMem ranges, no overlaps`
  after the revert.
- A `NoHob` alias attempt for the five regions was **rejected before the MMU was
  set up**: SEC printed `"XBL_DT" (0x81A00000) and "XBL_DTLOG_REGION" (0x...) Overlap!`
  for all five and stopped at `[12] InitializeMemory: HOB and MMU`. `NoHob` still
  participates in SEC's overlap check, so aliases cannot be added this way.

### Hardware observations from this round

- The platform resets ~23 s after `fastboot boot` on the original build. That is
  gone: every subsequent build stayed up for minutes and printed a diagnostic
  screen. The reset was fixed by the 4.0.6.0 driver extraction, and the aston
  switch has not yet been tested on hardware.
- The failure is always `ASSERT RETURN_ERROR (Status = Not Found)` at
  `AutoGen.c`. Skipping one driver moves it to the next, so it is the dispatcher
  entry-point check, not one bad driver.
- Screen text is drawn by `GraphicsConsoleDxe` onto the GOP framebuffer, which
  overwrites anything an earlier driver drew there. A diagnostic drawn early is
  gone by the time the assert appears, which is why `DtbExtensionHobDxe`'s
  `DTBHOB:` rows never showed. `DEBUG` from a myronPkg driver also does not reach
  the console even though the DXE core's own output does.

## 2026-11-17: kernel builds, links and seals

Recorded in full in `work/kernel-green.md`. Summary:

    kernel/kernel.macho   89423872 bytes
    sha256                68bcd2fbb0f9ec43a5f92be1092cdc025f4fc8b670f8410257576e1ae7eb736f

arm64e XNU 12377 for iOS 26.5.2 (23F84). The run is green on all five jobs.

Three things were needed to get there, each one a real defect:

1. **FIPS POST seal.** `scripts/seal-source-crypto-kernel.py` demands a
   `__TEXT,__fips_hmacs` section. It is defined by
   `cc_fips/src/fipspost_post.c`, which is in the `corecrypto_kext` target's 631
   sources but is pulled in lazily, because `build-xnu-4k-baseline.sh` attaches
   the crypto archive without `-Wl,-force_load` while every other archive has it.
   Force-loading the archive fixed the seal, and also supplied the real
   `corecrypto_kext_start`, which made my stub for it a duplicate symbol. Stub
   removed.
2. **`kernelcache_sha256` in the OS profile was wrong** from offset 17:
   `f3acf5ed` where the kernelcache has `fc3acf5e`. Corrected against the
   upstream profile.
3. **`early_security_init` in the OS profile was a different page**:
   `0xffffffe0008f13c8` instead of `0xfffffe0008f13c80`. Corrected.

A cross-check now compares every scalar this repository shares with the upstream
23F84 profile: 35 keys, one deliberate difference (`os.name` keeps the `ios-`
prefix), no accidental ones. This class of error has now happened twice
(`systemos_image_sha256` was 65 characters earlier), so it is checked by
comparison rather than by a CI run.

### Prelink, honestly

`emit_prelink.py` pairs 6805 of 21113 original functions (32%) and refuses to
emit. The native kernel has 29411 functions and 841 diversities against the
kernelcache's 21113 and 2885, a DEVELOPMENT build measured against Apple's
RELEASE-LTO. The 16306 in `Incomplete pairing` is the function-mapping deficit;
what actually remains unbound in the emitted closure is **808 unresolved
references with 1896 stubbed functions**, which is much smaller than the pairing
ratio suggests, because string anchors (3572), PAC-checked shapes (333), pure
leaves (116), vtables (980) and 108 reviewed anchors carry references even where
no function matched by hash.

`--allow-missing` (passed through `board.toml [kernel.prelink] extra_args`, which
`kernel.py` forwards to the emitter) writes the candidate and records every gap
in `kernel.macho.json`. The image is real; it is not bootable, and a boot is
expected to fail at the first rebound kext.

## 2026-11-15 to 2026-11-16: the reset was inside the platform package

`myron-backup/FINDINGS.md` established this before the current session. The
boot chain is sound: `fastboot boot` of the stock `boot_a.img` reaches Android,
a `wfe` loop hangs forever, BootShim copying the FD and jumping hangs forever.
The real firmware reset at ~23 s in both RELEASE and DEBUG, and the same-family
`songyuan` image did not, which localised the fault to `myronPkg`.

The suspected mechanism was the OnePlus ACPI tables and `Binaries/aston` vendor
DXE touching registers the SM8850 board does not have. That suspicion is now
**inverted by the 2026-11-17 entry**: the aston set is what the working sibling
uses, and the phone's own extracted drivers are what asserted.

## Standing rules earned from this log

- Never claim a status higher than the evidence. A green build is a green build.
- Before inventing a mechanism, read the sibling platform that already works.
  `dodge`, `annibale` and `songyuan` are all in the same family and all public.
- When a device-side theory fails twice, stop and diff the configuration against
  a working one instead of reasoning further from first principles.
- Keep the failed attempt and its evidence. Two of this session's detours
  (memory map rename, `NoHob` aliases) are only useful if their rejection is
  written down.
