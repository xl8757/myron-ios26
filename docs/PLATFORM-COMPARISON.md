# Platform package comparison across SM8850/SM8750

The point of this file: when `myronPkg` misbehaves, there is almost certainly a
public sibling that behaves. Read the sibling before inventing a mechanism.

## The candidates

| repo | device | SoC | state |
| --- | --- | --- | --- |
| `aogg2023/songyuan-uefi` + `aogg2023/songyuan-device` | Redmi K90 Pro Max (`songyuan`) | SM8850 Canoe | **boots iPadOS 17.7.11 to SpringBoard**; touch, Metal, persistent Data |
| `iola1999/hackios-mu-silicium` (`annibalePkg`) | Redmi K90 (`annibale`) | SM8750 | HackiOS, mature: icons, audio, Bluetooth, video |
| `Project-Silicium/Mu-Silicium` (`dodgePkg`) | OnePlus 13R (`dodge`) | SM8750 | upstream reference; `annibalePkg` is a copy of it |
| `NiandeNian/songyuan-uefi` + `-device` | Redmi K100 Pro Max | SM8850 Canoe | in progress; firmware menu still black |
| this repo (`myronPkg`) | Redmi K90 Pro Max (`myron`) | SM8850 Canoe | firmware runs, DXE asserts |

`myron` and `songyuan` are the same hardware. That makes `songyuanPkg` the single
most useful reference available, and it is the one this repository now follows.

## What songyuanPkg does, and what myronPkg did

Verified against the files in each repository, not from memory.

| aspect | songyuanPkg | myronPkg before | myronPkg now |
| --- | --- | --- | --- |
| vendor binaries | `Binaries/aston` (Mu submodule) | `Binaries/myron` (extracted from phone) | `Binaries/aston` |
| INF layout | `Binaries/aston/QcomPkg/Drivers/<N>Dxe/<N>Dxe.inf` | `Binaries/myron/<N>/<N>.inf` (flat) | `Binaries/aston/...` |
| EnvDxe | `EnvDxe.inf` | `EnvDxeEnhanced.inf` | `EnvDxe.inf` |
| DT publisher | none | custom `DtbExtensionHobDxe` | none |
| DTB FREEFORM | commented out | present | commented out |
| `DtPlatformDxe` | commented out | absent | commented out |
| `USE_CUSTOM_DISPLAY_DRIVER` | 1 | 0 | 0 |
| framebuffer PCD | 1264x2780 | not set | not set |
| ACPI tables | `aston/Acpitables.inf` | same | same |
| raw files | `Binaries/aston/RawFiles/*` (58) | `Binaries/myron/RawFiles/*` (111) | `Binaries/aston/RawFiles/*` |

Driver name spelling follows the Mu-Silicium convention, not the phone's
firmware: `ChipInfoDxe`, `DALSYSDxe`, `HWIODxe`, `I2CDxe`, `TLMMDxe`,
`PmicDxeLa`, `SPMIDxe`, `HALIOMMUDxe`, `PlatformInfoDxe`, `GLinkDxe`. The
extracted set used `ChipInfo`, `DALSys`, `HWIODxeDriver`, `I2C`, `DALTLMM`,
`PmicDxe`, `SPMI`, `HALIOMMU`, `PlatformInfoDxeDriver`, `GlinkDxe`.

Seven drivers exist in myronPkg's list but have no aston counterpart and are not
in songyuanPkg either: `EXFG`, `I3CMaster`, `MailboxDxe`, `QcomScmiDxe`, `Qup`,
`SPI`, `XhciPciEmulation`. They were dropped.

## The `Binaries` submodule

`Project-Silicium/Mu-Silicium/.gitmodules`:

    [submodule "Binaries"]
      path = Binaries
      url = https://github.com/Project-Silicium/Device-Binaries.git

`Device-Binaries` holds 77 device directories. Mu-Silicium builds against them
with no extra step, which is why `songyuan-uefi`'s CI contains no vendor fetch:
it clones Mu-Silicium recursively, overlays its `songyuanPkg`, and builds.

Any workflow that extracts drivers from the phone's own firmware is doing
something the upstream path does not do, and has to justify why.

## Where the device tree stands

Both sibling packages this phone boots from **disable** the device tree path:

    # songyuan.fdf
    # Device Tree
    #INF EmbeddedPkg/Drivers/DtPlatformDxe/DtPlatformDxe.inf
    #FILE FREEFORM = 25462CDA-221F-47DF-AC1D-259CFAA4E326 {
    #  SECTION RAW = songyuanPkg/FdtBlob/...
    #  SECTION UI = "DeviceTreeBlob"
    #}

The reason is in Mu's `PlatformPei.c`: `GetXblHobAddresses` scans the `FD
Reserved` region for the stock UEFI's HOB registration function, and only builds
`gEfiDtbExtensionHobGuid` `if (DtbExtensionAddr)`. Under `fastboot boot` that
region holds ABL leftovers, the scan finds nothing, and no HOB is created.
`ModuleEntryPoint.S` also overwrites `x0` with `EL1_OR_EL2 (x0)` before anything
records the bootloader's argument, so the address cannot be rescued from there
either.

Publishing the HOB from an embedded blob is possible - that was tried here and
described in `docs/DXE-REGION-NAMES.md` - but it did not change the assertion,
and the sibling that works does not need it.

## Checks worth running before any change

1. Does a sibling for this SoC exist, and what does its platform package list?
2. Are the vendor binaries the upstream submodule set, or extracted locally?
3. Does the driver spelling match `Binaries/<device>/QcomPkg/Drivers/...`?
4. Is the change touching the device tree, and does the working sibling?

Answering those four questions would have saved this session's two detours.
