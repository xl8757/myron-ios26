# Why the vendor DXE stack asserts under `fastboot boot`

> **SUPERSEDED - do not act on section 5.** The naming comparison in this
> document is still correct as an observation, and section 6 holds. The
> conclusion - that the region names were the cause and should be aliased or
> renamed - is **wrong**, and so is the fix it proposes.
>
> `aogg2023/songyuan-uefi` is the same phone (Redmi K90 Pro Max, SM8850) and it
> boots to SpringBoard with `XBL_DTLOG_REGION`, `SMEM_REGION` and
> `AOP_IMAGE_REGION` unchanged. The panel was not the cause.
>
> What actually differs is the vendor binary set: the working package uses
> `Binaries/aston` from the Mu-Silicium submodule, this repository was using the
> phone's own extracted DXE drivers, and `EnvDxeEnhanced.efi` returns
> `EFI_NOT_FOUND` from the extracted set. See
> [`work/BRINGUP-LOG.md`](../work/BRINGUP-LOG.md), entry 2026-11-17.
>
> The rename was applied, verified present in the firmware, and made no
> difference to the assertion. It has since been reverted, as has the
> `NoHob` alias variant, which SEC rejects with an overlap report before the MMU
> is set up.


Status on 2026-11-17: root cause identified by comparison against a working
sibling platform, fix not yet applied.

## 1. The symptom

Booting the myron UEFI payload with `fastboot boot` stops in DXE with

    ASSERT RETURN_ERROR (Status = Not Found)
    ASSERT AutoGen.c +652: !RETURN_ERROR (Status)
    Lock handle not initialized
    ASSERT spinlock.c +108: Lock handle not initialized

The screen names the module: `EnvDxeEnhanced.efi`, image size `0x13000`. Skipping
that driver moves the failure to `SmemDxe.efi` at `AutoGen.c +384`, and the
custom driver that was meant to publish the device tree reported

    DTBHOB: FAIL no entry argument HOB; SEC did not record x0

`Status = Not Found` means the driver's entry point returned `EFI_NOT_FOUND`
because a protocol or a named resource was missing. The spinlock message is a
consequence: the ASSERT handler tried to take a lock that was never set up.

## 2. What was ruled out

Each of these was tested on hardware and is not the cause.

| Hypothesis | Result |
| --- | --- |
| Wrong vendor firmware version | 4.0.6.0 and 3.0.13.0 assert at the same module and the same line, so the version does not matter |
| Missing device tree in the image | `myron.fdf` already embeds `myronPkg/FdtBlob/myron.dtb` as FREEFORM `25462CDA-...`, UI `DeviceTreeBlob`, 581416 bytes; verified present in the built volume |
| Missing standard DT driver | `EmbeddedPkg/Drivers/DtPlatformDxe` builds and links fine, and adding it changed nothing |
| ABL not passing a device tree | Mu's `ModuleEntryPoint.S` runs `EL1_OR_EL2 (x0)` before anything records the argument, so the passed address is gone regardless |

The last row matters: publishing the HOB from the embedded image is the right
shape of fix, but it is not sufficient on its own, because the lookup that fails
is not the tree lookup.

## 3. The actual cause: region names

`LocateMemoryRegionByName` in `SiliciumPkg/Library/MemoryMapHelperLib` matches
names exactly:

    if (AsciiStriCmp (RegionName, MemoryDescriptor[i].Name)) {
      continue;
    }

The vendor drivers ask for Qualcomm BSP names. `IortDtDxe`, for example, is
written against three candidates:

    STATIC CHAR8 *XblDtMemoryName[] = {"DT BLOB", "XBL DT", "XBL_DT"};

The myron `MemoryMapLib.c` table is generated from the live myron device tree, so
its entries carry the reserved-memory node names instead:

    {"XBL_DTLOG_REGION", 0x81A00000, 0x00060000, ...}

`XBL_DTLOG_REGION` matches none of the three candidates. The lookup returns
`EFI_NOT_FOUND`, the driver's entry point propagates it, and the dispatcher
asserts.

## 4. The comparison that proved it

Both platforms describe the same physical memory. Only the labels differ.

| address | size | annibale (works) | myron (asserts) |
| --- | --- | --- | --- |
| `0x81A00000` | 0x40000 / 0x60000 | `XBL_DT` | `XBL_DTLOG_REGION` |
| `0x81C00000` | 0xA0000 / 0x60000 | `AOP` | `AOP_IMAGE_REGION` |
| `0x81D00000` | 0x200000 | `SMEM` | `SMEM_REGION` |
| `0x81F00000` | 0x100000 | `PDP_NS` | `PDP_NS_SHARED_REGION` |
| `0x824A0000` | 0x100000 | `PvmFw` | `PVM_FW_REGION` |

The header comment in myron's table says where the names came from:

    // Stock reserved-memory nodes from the live myron FDT. Keep these exact:
    // exposing any of them as conventional memory lets DXE overwrite active firmware.

That intent - never expose stock reserved memory as conventional - is correct and
must be preserved. The defect is only that the BSP names are gone.

annibale's table is hand-written against the BSP vocabulary ("DDR Regions"), so
its names happen to be the ones the drivers look for.

## 5. The fix to apply

Add alias entries next to each renamed region, keeping the existing entry and its
flags untouched:

    // The BSP vocabulary the vendor binaries are built against. Same address and
    // the same Reserv/attribute flags as the entry above; IortDtDxe looks up
    // "XBL DT"/"XBL_DT" and SmemLib looks up "SMEM", both by exact name.
    {"XBL_DT", 0x81A00000, 0x00060000, AddMem, MEM_RES, SYS_MEM_CAP, Reserv, WRITE_BACK_XN},

Aliases to add: `XBL_DT`, `AOP`, `SMEM`, `PDP_NS`, `PvmFw`.

Notes on the change:

- `gMemoryDescriptor` is a flat array that `GetMemoryMap` returns wholesale, and
  the lookup is a linear scan, so several entries may share an address.
- The alias must copy the original entry's flags verbatim. `SMEM` in myron is
  `UNCACHEABLE / UNCACHED_UNBUFFERED_XN`; publishing it with different attributes
  would change how the region is mapped, which is worse than the bug.
- `AddMem` with a duplicate address adds another memory descriptor for the same
  range. It does not move anything, but `validate_memory_map.py` should be rerun
  because it checks for overlaps.

## 6. Open questions before calling it fixed

- Does `EnvDxeEnhanced` itself look up a name this table does not carry at all,
  rather than a renamed one? The table has 77 entries against annibale's 40, so
  absent names are plausible too.
- `XBL_DT` in annibale is `0x40000`; myron's region at that address is
  `0x60000`. If the BSP path reads past the smaller size the assertion could move
  rather than disappear, so the alias should keep myron's larger size.
- The myron table has no `PIL_Reserved`, no `DBI_Dump`, no `Display_Demura` and no
  `APSS_HM`. Whether any driver requires those is unknown.

## 7. How this was found

`myron-backup/FINDINGS.md` had already established that the reset was inside the
platform package, not in BootShim or the FD wrapper. This round narrowed it
further by reading a working sibling: `iola1999/hackios-redmi-k90-annibale` is the
same Xiaomi K-series family on SM8750, and its platform package is named in its
own docs as a copy of OnePlus 13R `dodgePkg`. Comparing its `MemoryMapLib.c`
against myron's is what exposed the naming split; comparing `APRIORI.inc` first
(the two differ only in which DT driver sits at position 3) pointed at `IortDtDxe`,
and `IortDtDxe.c` is the file that names the strings it looks up.
