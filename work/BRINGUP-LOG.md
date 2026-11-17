# Bring-up log

Reverse chronological. Every entry says what was observed, on what evidence, and
what it means for the next attempt. Hardware observations are separated from
build observations on purpose: a green build proves nothing about the phone.

## 2026-11-17: the vendor ABI was the problem, not the device tree

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
