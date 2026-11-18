# Getting a payload onto this board

This records what is known about how the payload reaches `UniXNURamDiskDxe` on
the Redmi K90 Pro Max, what has been tried on hardware, and why each route that
looks available is not. Everything here is measured, not inferred, unless it is
marked otherwise.

## How the mechanism is supposed to work

`UniXNU`'s `docs/porting/uefi-firmware.md` describes one route:

> The Android bootloader places the FAT payload in RAM and writes
> `/chosen/linux,initrd-start` and `linux,initrd-end` into the firmware FDT.

`Mu-Silicium`'s SEC preserves the incoming FDT pointer in an entry-argument HOB
(`DAEE82E6-8090-4E72-BFB9-78C13CD4E359`), and `UniXNURamDiskDxe` reads the initrd
range out of `/chosen`. No FDT means no address, which is why the driver also
carries a fixed-address fallback.

## What actually happens here

The bootloader passes no device tree. Both `fastboot boot` and a flashed
`boot_a` followed by a normal restart produce the same handoff:

    DTBHOB: no entry argument HOB, ABL passed no tree
    UNIXNURAMDISK: x0 0x0 is not a device tree

`BootShim.S` does not touch `x0`, so this is what ABL itself hands over. The
`/chosen` path is therefore closed, and only the fixed-address fallback remains.

### Address probes on hardware

| address | what it is | result |
| --- | --- | --- |
| `0x01000000` | the raw `ramdisk_addr` field from the boot image | **synchronisation exception** |
| `0xA6F00000` | `Initrd_Small` | empty |
| `0xF0000000` | `XNU_Payload` (56 MiB) | empty |
| `0xB8000000` | `DXE_Heap` | empty |
| `0xA8000000` | `Kernel` | empty |
| `0xF8800000` | `OEM_VM_REGION` | empty |
| `0x81000000` | `DRAM_BASE + 0x01000000` | **never probed with a clean image** |

`0x01000000` faults because DRAM on this SoC starts at `0x80000000`; everything
below that is MMIO, and `MemoryMapLib` does not declare it. That is worth
separating from "the bootloader did not load anything": the first is about
page tables, the second about what ABL did.

`0x81000000` is the interesting one. The stock `vendor_boot.img` says
`kernel_addr = 0x8000` and `ramdisk_addr = 0x1000000`. Neither is a DRAM address,
yet that image boots, so ABL must be adding `DRAM_BASE` to those fields. If it
does the same for this board's header-v0 image, the payload is at
`0x80000000 + 0x01000000 = 0x81000000`. The build that carried this probe
(`79b3335`) also carried the vendor DXE set and stopped in `EnvDxe` before the
probe ran, so the address has never actually been tested.

## The embedded route, and why it does not fit

`myron.fdf` declares the payload as a FREEFORM RAW section inside `FvMain`:

    FILE FREEFORM = A1B2C3D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D {
      SECTION RAW = myronPkg/Payload/payload.img
      SECTION UI = "PayloadVolume"
    }

and `FVMAIN_COMPACT` embeds `FvMain` through an LZMA GUIDed section:

    FILE FV_IMAGE = 9E21FD93-9C72-4c15-8C4B-E77F1DB2D792 {
      SECTION GUIDED EE4E5898-3914-4259-9D6E-DC7BD79403CF PROCESSING_REQUIRED = TRUE {
        SECTION FV_IMAGE = FVMAIN
      }
    }

So the FD only stores the compressed size, which is what made embedding look
affordable: an 11 MiB payload with a 9.84 MiB kernel is about 2.78 MiB of LZMA
data, and the volume was grown from 3 MiB to the 5 MiB that fits between
`0xA7100000` and `Info_Blk` at `0xA7601000`.

What that reasoning missed is the decompression target. SEC decompresses
`FvMain` into RAM before any driver inside it can be reached, and it needs the
whole uncompressed size at once:

| payload | `FvMain` decompressed | FD | observed |
| --- | --- | --- | --- |
| 1 MiB | ~3 MiB | 3 MiB | menu appears |
| 16 MiB | ~18 MiB | 5 MiB | black screen, falls back to Android |

The memory map cannot supply 18 MiB for this. The regions around the volume are
`FV_Region` 4 MiB, `Sched_Heap` 4 MiB and `UEFI_RESV` 1.2 MiB, and `DXE_Heap`'s
189 MiB starts at `0xB8000000`, far from where the decompressor can place
anything. Decompression fails, no DXE driver loads, and the phone drops back to
Android. This is why a kernel-sized payload cannot ride in `FvMain` on this
board.

Moving it out of `FvMain` is not a one-line change either. The FDF comment states
the constraint: `UniXNURamDiskDxe` finds the section with
`GetSectionFromAnyFv`, which only searches firmware volumes that already have the
FV2 protocol installed. `FVMAIN_COMPACT` is the SEC decompression layer and does
not have it, so the payload has to be in `FvMain` for the lookup to work at all.

## Constraints worth keeping in mind

- The FD window is bounded below by `Initrd_Small` at `0xA6F00000` and above by
  `Info_Blk` at `0xA7601000`. It grows upward from `0xA7100000`. Moving its base
  down onto `Initrd_Small` puts the copied firmware on top of the boot image
  ramdisk and black-screens; that was tried.
- `Initrd_Small` is 1 MiB and ends at `FD_Reserved`, so it cannot be enlarged in
  place. `ADSPSLPI_REGION` ends at `0xA6980000`, leaving 5.5 MiB of gap below it.
- The `boot` and `vendor_boot` partitions are 96 MiB each, `init_boot` is 8 MiB.
  A 16 MiB payload does not fit `init_boot`.
- The linked kernel is 16.6 MiB and `__LINKEDIT` is 6.48 MiB of that.
  `tools/strip-macho.py` drops the symbol table and the section it describes,
  giving 9.84 MiB with every segment's `vmaddr` and `vmsize` unchanged.
- `make_payload.py` writes FAT12 with a configurable cluster size. At one sector
  per cluster the volume ceiling is 2 MiB; 16 sectors (8 KiB) gives 31.9 MiB.
  Directory entries are 8.3, so the kernel file is `KERNEL.BIN`, not
  `KERNEL.MACHO`.
- The release notes do not record `vendor`, and no `fd-` release was published
  from a `vendor=none` dispatch. `vendor=full` stops in `EnvDxe` on this board
  before any menu appears, so the build has to be dispatched with
  `-f vendor=none`.

## Open questions, in the order they should be answered

1. **Does ABL honour the header's `ramdisk_addr` on this board at all?** The
   stock images suggest it adds `DRAM_BASE` to the field first, which would make
   the payload address `0x81000000`. Test it with a `vendor=none` build that
   probes that address and nothing else.
2. If it does not, is there any path by which a ramdisk reaches this firmware?
   `fastboot boot` and a flashed `boot` both produced `x0 = 0`, so the FDT route
   was never available to begin with.
3. If no ramdisk ever arrives, the payload has to be produced by the firmware
   itself, and the RAM for it has to be reserved somewhere the memory map can
   describe. The volume's own decompression requirement rules out `FvMain`.

## Reproducing what is built

    # firmware, always with vendor=none or it stops in EnvDxe
    gh workflow run build.yml --repo xl8757/myron-uefi \\
      -f display=simple -f vendor=none

    # the kernel asset the payload step embeds
    gh release download kernel-9.84mib --repo xl8757/myron-uefi \\
      --pattern kernel-minimal.macho

Offline checks that have been run and pass: the FD is 5,242,976 bytes and equals
`[uefi_fd] size` in `myron.toml`; the payload volume inside it is FAT12 with
`KERNEL.BIN` present and the Mach-O magic at offset 80384; the `boot.img` kernel
section decompresses to exactly `shim + FD`. None of that proves the volume is
reachable at runtime, because `FvMain` cannot be decompressed.
