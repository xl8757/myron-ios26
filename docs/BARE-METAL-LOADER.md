# The bare-metal loader route

This is the second architecture tried for this board. The first was the UniXNU UEFI
firmware, which needs a payload it can read from the boot image ramdisk or from the
firmware volume; both were measured on hardware and neither works (see
`PAYLOAD-DELIVERY.md`). This route removes the question instead of answering it.

The approach follows `ShueiTsuki/data-a-live`, which boots native iOS 16 on the same
SoC (SM8850). Their repository is the reference for every measurement below that is
not marked otherwise.

## Shape

```
ABL
 └─ loads one arm64 Image (header v4, no ramdisk)
     ├─ _head           standard ARM64 Image header, magic "ARM\x64" at 0x38
     ├─ loader code     freestanding, linked at 0, no absolute relocations
     ├─ .logo           optional boot image (empty here: black screen)
     └─ .payload        the XNU payload, .incbin'd, 4 KiB aligned

loader:
  1. masks interrupts, installs exception vectors, clears .bss
  2. copies every kernel segment to phys_base + (vmaddr - virt_base)
  3. copies the ADT behind the last segment
  4. builds boot_args one 16 KiB page after that
  5. dsb sy; ic iallu; branches to the kernel entry with the MMU off
```

XNU gets `x0 = boot_args` in physical memory. The MMU is off at entry, so the loader
treats every access as Device memory and compiles with `-mstrict-align`.

## The payload

`mkpayload.py` packs the kernel and the ADT into one blob:

```
0x000  char  magic[8] = "SWEETXNU"
0x008  u32   version = 1, nsegs
0x010  u64   virt_base      kernel VA that maps to phys_base
0x018  u64   entry_va       LC_UNIXTHREAD pc
0x020  u64   adt_off, adt_size
0x030  u64   kernel_end_va  end of the highest segment, page aligned
0x038  u64   phys_base, mem_size
0x048  char  cmdline[608]
0x2a8  seg[nsegs] { u64 vmaddr, vmsize, file_off(in payload), file_size }
```

The kernel is currently 10 segments and 17,411,056 bytes with the ADT.

**The chained fixups stay in the payload.** `mkpayload.py` as shipped by ios-sweet
walks `LC_DYLD_CHAINED_FIXUPS` and rewrites every pointer to a plain VA, because
xnu-8796 does not do that itself. XNU 12377 does:

> XNU rebases and signs its own fixup chains after entry; firmware only copies root
> mappings. - `DarwinBootPkg/Driver/DarwinBoot.c`, UniXNU/DarwinBoot

and DarwinBoot's own segment copy is a plain `CopyMem` of the file bytes. Passing a
pre-rewritten kernel to this XNU hands it data that has already been through the
rebasing step. `--no-fixups` keeps the chains; the payload's `__TEXT` then matches the
source kernel byte for byte over the first 4 KiB, which is how the build verifies it.

The kernel must be the **unstripped** one. `LC_DYLD_CHAINED_FIXUPS` points into
`__LINKEDIT`; the 9.84 MiB stripped kernel used by the UEFI route deleted the bytes it
names, so the chains cannot be walked at all.

## Memory

| what | value | why |
| --- | --- | --- |
| `phys_base` | `0x890000000` | see below |
| `mem_size` | `0x36ff00000` (13.75 GiB) | to the end of the high bank |
| framebuffer | `0xFC800000`, 1200x2608 | `Display_Reserved` in `MemoryMapLib` |
| GICD / GICR | `0x17000000` / `0x17080000` | `dts/myron-handoff.dts` |
| redistributor stride | `0x40000` | same |
| timebase | 19200000 | same |

The board's DRAM, from its own boot log:

```
0x80000000  + 0x80000000   2 GiB, low bank
0x880000000 + 0x380000000  14 GiB, high bank
```

The low bank is full of Qualcomm carve-outs (MPSS 235 MiB, ADSP 85 MiB, DSM 95 MiB,
and so on), and XNU maps every byte from `phys_base` upward as cacheable memory, so
one protected range inside that span kills the kernel. In the high bank there is
exactly one such range:

```
LCP_DARE_REGION  0x880000000 + 0x06000000   end 0x886000000
```

`phys_base` therefore starts at `0x890000000`, 160 MiB past the end of it. This is the
rule ios-sweet records as "every protected range must sit below the kernel". The first
build used the bank base and the phone went silent; the second uses this value and
gets further, so the loader prints `PHYS` for every boot.

## What the loader prints, and how to read it

The framebuffer is the only channel before the kernel takes over. Two boots on
hardware, with the address arithmetic checked against the payload header offline:

```
PANDORA  loader v2 - nothing is written to storage
LEVEL    EL1                              exception level; XNU needs EL1
LOADED   0x00000000C7800000               where ABL put the image
PAYLOAD  0x00000000C780B000               LOADED + payload offset in the image
PHYS     0x0000000089000000               phys_base from the payload
MEMSIZE  0x000000036FF00000               mem_size from the payload
ENTRY    0xFFFFFFF00734D488               entry VA from LC_UNIXTHREAD
KERN_END 0x0000000892150000               PHYS + (kernel_end_va - virt_base)
BOOTARGS 0x0000000892154000               KERN_END + ADT, rounded to 16 KiB
JUMP     0x000000089134D488               PHYS + (entry_va - virt_base)
XNU      jumping (the kernel console takes over)
```

`KERN_END`, `BOOTARGS` and `JUMP` were each recomputed from the payload and match.
When the screen stops here and stays blue, the loader worked and the kernel died
before it could print; the phone does not reset, so it must be power-cycled by hand.
That is the state the ADT work below is for.

## Where it fails now: the ADT belongs to the wrong kernel

This build used ios-sweet's `mkadt.py`, a hand-written device tree for xnu-8796.
UniXNU generates its ADT from a compiled hardware DTB plus the OS profile
(`scripts/make-adt.py` and `scripts/unixnu/devicetree.py`), and the difference is
decisive. From `devicetree.py`:

```python
# xnu-12377 takes 4 x 64 seed bytes on ARM and panics with fewer;
# older kernels read the first 64.
chosen = [('random-seed', os.urandom(256)), ('dram-base', ...), ...]
```

The ADT this build generated carries a **64-byte** `random-seed`; the length field is
`0x40` in the payload next to the property name. XNU 12377 panics on that before any
console exists. The same generator also supplies things the ios-sweet tree has no
concept of:

- `empty_nvram_proxy()` - the 8 KiB CHRP bank IODTNVRAM consumes
- `/chosen/memory-map` and `/chosen/manifest-properties`
- `darwinboot-memory-size` and friends, which only a profile whose XNU reads them may
  request

and it reads `/cpus`, `/psci`, `/memory` (one contiguous range) and the `arm-io`
children out of the hardware tree, so a compiled `myron.dtb` is required input.

## Building

```
# build the loader image (CI: .github/workflows/build-loader.yml)
KERNEL=xnu-12377-arm64e.macho tools/loader/build.sh
```

`build.sh` needs an AArch64 clang and `ld.lld` able to target `aarch64-none-elf`.
It refuses to link if `loader.c` produced any `R_AARCH64_ABS` relocation, because the
image is linked at 0 and loaded wherever ABL chooses.

The kernel comes from the `kernel-16.6mib` release in `myron-uefi`. It must be the
unstripped image.

## Open items

1. Generate the ADT with UniXNU's `make-adt.py`, which needs a compiled `myron.dtb`.
   `dts/myron.dts` is in the repository; no `dtc` is installed on the development
   machine, so this has to happen in CI or with a locally built `dtc`.
2. Pass `--unique-chip-id` (the board's `payload.adt.unique_chip_id` is `required`).
3. Only then is it worth looking at what the kernel says next. The framebuffer is
   initialized by the loader and handed over in `boot_args.Video`, so the first kernel
   output should land on the screen.
