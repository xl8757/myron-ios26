# UEFI firmware for myron

`[firmware]` in `board.toml` names the Mu-Silicium platform package from the
sibling `myron-uefi` repository:

```
config = "myron"
dsc    = "Platforms/Xiaomi/myronPkg/myron.dsc"
fd     = "Build/myronPkg/{target}_CLANGPDB/FV/SILICIUM_UEFI.fd"
```

## What exists

`myron-uefi` CI run 43711284872 published release `fd-20261115-120153-2fda1c2`:

| | |
| --- | --- |
| `SILICIUM_UEFI.fd` | 8,388,608 bytes (`0x800000`) |
| sha256 | `6dc6760fd9fb0a5e27e3ad7056015d82c8d07686bf8815bb529719e922651f59` |
| Vendor DXE modules | 56, listed as `INF Binaries/myron/...` in `DXE.inc` |
| FVMAIN inside the released volume | decompresses to 10,379,272 bytes |

The volume contains `UFSDxe`, `ClockDxe`, `ChipInfo`, `DDRInfo`, `Smem`,
`DALTLMM`, `QcomScmiDxe`, `ShmBridgeDxeLA`, `IortDtDxe`, `DtbExtensionHobDxe`,
`UniXNURamDiskDxe`, `DarwinBoot`, and the `PayloadVolume` /
`BigPayloadSparse` `FREEFORM`s.

This is the same firmware regardless of the OS profile: it depends on the board
and the SoC, not on whether the payload boots iOS 16 or iOS 26. That is why the
iOS 26 migration did not touch this section.

## What has not been verified

The volume has **never been `fastboot boot`ed**. It is a firmware volume, and
the boot chain still needs `unixnu bootimg` to pack BootShim, the payload and
the device tree into a `boot.img`. Nothing in this repository has produced that
image for the iOS 26 target.

Two inherited unknowns, both recorded in `PENDING.json`:

- The package's ACPI tables are the OnePlus 15 (`aston`) set, and whether the
  `aston` driver binaries fit myron's peripherals is unknown.
- The panel is not re-initialised; the firmware framebuffer is used as-is and
  refresh is up to the layer above.

## The preflight script

`[firmware] preflight = ["scripts/check-myron-firmware.py"]` is still a
placeholder. The file present in this repository is
`scripts/check-nx769j-firmware.py`, which is board specific to the NX769J: it
checks that board's vendor driver set and partition assumptions. Pointing the
preflight at it would check the wrong board and pass for the wrong reasons.

Porting it is tracked in `PENDING.json` under `firmware.preflight`. Until then a
build that runs the preflight step fails loudly on the missing file rather than
silently accepting a wrong vendor input, which is the safer of the two failure
modes.
