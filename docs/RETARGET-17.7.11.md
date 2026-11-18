# iPadOS 17.7.11 retarget: what was learned, and why the board file stays on 23F84

This records a day of investigation into retargeting this board from iOS 26.5.2
(23F84) to iPadOS 17.7.11 (21H461). The conclusion is in the last section: the
retarget was attempted, the reference material was read, and the board file was
returned to 23F84 because the 17.7.11 path needs a device repository that does
not exist in a buildable form.

Read `work/BRINGUP-LOG.md` first for how the iOS 26 work got here.

## Why the retarget looked right

`aogg2023/songyuan-device` is the same phone: Redmi K90 Pro Max, SM8850 Canoe,
`kit = "myron"`, and its UEFI port is `Platforms/Xiaomi/myronPkg` - the same
package name this repository builds. Its README states the tree boots iPadOS
17.7.11 (iPad7,1 userland) to SpringBoard, with touch, Adreno 840 Metal through
NativeMetal/Turnip, and a persistent UFS Data store.

Its kernel configuration is not the one this repository had been chasing:

| | songyuan (boots) | myron-ios26 (ours) |
| --- | --- | --- |
| OS target | iPadOS 17.7.11 (21H461) | iOS 26.5.2 (23F84) |
| Identity | iPad7,1 / j120ap | iPad17,3 / j820ap |
| `[kernel.prelink].kernel` | DEVELOPMENT | RELEASE (changed during this work) |
| `--allow-missing` | absent | present |
| prelink kit | `kits/NativePrelink` | `kits/XnuPrelink` |

`NativePrelink` and `XnuPrelink` are the same repository: `emit_prelink.py`,
`fileset_match.py` and `kc_image.py` have identical sha256 in both. The
repository was renamed. So the kit was never the difference.

UniXNU's 17.7.11 support is complete and exercised. `profiles/os/ipados-17.7.11-21H461/profile.toml`
carries the kernelcache pin (`f18b4df8...`), the 31 roots shot,
`early_security_init` for `AppleMobileFileIntegrity=0xfffffff005ab4ae0`, the two
trust cache hashes, the payload `text_fixups`, and the whole ADT identity block
(`iPad7,1`, `J120`, `J120AP`, `chip_id 32785`). The 23F84 profile exists too but
its prelink fixture is 108 functions.

## What the reference repository contains, and what it does not

`aogg2023/songyuan-device` is 71 files. 67 of them are absent from this
repository:

- `package/` - 27 C/C++ sources and headers, 528 KB total. Touch (`MyronTouch.cpp`,
  `MyronTouchSched.c`), GPU (`MyronGmu.c`, `MyronHfi.c`, `MyronGpuPower.cpp`,
  `MyronGpuBoard.h`), display (`LegacyDisplayProbe.cpp`), input
  (`HidInputProbe.cpp`), keystore, command DB (`MyronCmdDb.c`), PMGR
  (`ShengPlatformPower.cpp`), and the on-screen diagnostic page (`BootProgress.c`
  plus seven headers).
- `scripts/` - 22 build scripts including `build-arm-driver.py`, `make-myron-adt.py`,
  `extract-myron-dtb.py`, `prepare-pmgr-reference.py`, `build-boot-progress.py`.
- `configs/songyuan_defconfig`, `configs/myron_defconfig`.

**It is not a buildable snapshot.** Its own `board.toml` names two archive
scripts that are not in the tree:

    build-native-aes-bindings.py
    build-native-virtio-bindings.py

`scripts/data/` is present but empty, so the `myron-observed.json` it references
is missing. `Platforms/Xiaomi/songyuanPkg/songyuan.dec` is a 0-byte file. The XNU
patch series the profile needs is not carried. Fetching these from UniXNU covers
some of them (`build-corecrypto-14.py`, `build-verified-root-probe.py`,
`build-sheng-source-storage.py` exist there) but not the two bindings scripts.

So the reference repository documents a working phone; it does not reproduce one
from a clean checkout.

## What the reference implementation says about our own configuration

Reading `scripts/make-myron-adt.py` (229 lines) was the valuable part, because it
is the generator that reached SpringBoard on this phone.

    DRAM_BASE         = 0x80000000
    ARMIO_BASE        = 0x17000000
    GIC_DIST          = 0x17000000   size 0x10000
    GIC_REDIST        = 0x17080000   size 0x200000
    GIC_REDIST_STRIDE = 0x40000
    TIMEBASE          = 19200000

Those are exactly the values in this repository's `dts/myron-handoff.dts`, and
the file's header already says it was copied from this generator. The GIC stride
is `0x40000`, not `0x20000`, and both our trees declare it correctly
(`dts/myron.dts` carries the stock bytes `00 00 00 00 00 04 00 00`).

The generator's `--cpu-regs` **defaults to `'0'`** - one core, the boot CPU. Its
README says secondary CPU startup needs the `native-smp-wide-affinity` XNU patch
and `sheng-smp=1`, and that the default arguments start the boot CPU only. So a
single-CPU device tree is the intended configuration for a first boot, and the
`GIC_REDIST_SIZE = 0x200000` window covering eight frames is completeness rather
than a requirement.

### A correction this reading forced

Earlier in this work the failure `FAR 0x17422030` inside `EnvDxe` was attributed
to the GIC: at stride `0x20000` that address is frame 29, beyond the 16 frames
the tree was said to describe. At the real stride `0x40000` it is frame 14 offset
`0x22030`, and the declared window (`0x17080000 + 0x200000 = 0x17280000`) does
not contain it either - so the arithmetic looked like a walk past the end of the
GICR region.

**That inference does not hold.** The crash only appeared in the build where
`DisplayDxe` was active together with a 1200x2608 framebuffer; the same firmware
with `1264x2780` ran for tens of seconds. A single-core tree is the documented
first-boot configuration, so a core-count mismatch is not the explanation, and
"the address lands near the GICR region" is a coincidence rather than a cause.
The display path is the variable that was actually observed to change the
outcome, as `work/BRINGUP-LOG.md` already records.

## Why the board file stays on 23F84

Retargeting needs, at minimum: an OS profile copied into `os-profiles/`, a
defconfig matching the 17.7.11 fragment set (33 settings against our 13), the
17.7.11 ATD identity, the iPad7,1 IPSW, and the 27 `package/` drivers plus the
20 build scripts. The last two groups do not exist here and the reference
repository does not carry all of them either.

The iOS 26 configuration, by contrast, builds today. Its kernel links, prelink
emits a candidate, and the pairing ratio is measured at 41.2% on the reference
path with `diversities` 811 - not enough to boot, but real and reproducible.

So `board.toml` is returned to `ios-26.5.2-23F84` with the iOS 26 identity, and
this document preserves what the 17.7.11 investigation produced:

- the exact hardware constants the working port uses, which match ours;
- the confirmation that one-CPU first boot is intended;
- the list of 67 files a retarget would need;
- the knowledge that the reference repository is documentation, not a
  reproducible build.

If UniXNU's 17.7.11 support is ever ported into this repository, the starting
point is `scripts/make-myron-adt.py` and `package/` from `aogg2023/songyuan-device`,
plus the two missing bindings scripts, which have to be written rather than
copied.
