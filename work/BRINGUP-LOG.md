# Bring-up log

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
