# Migrating this board from iOS 16.7.16 to iOS 26.5.2

This repository was an iOS 16.7.16 / XNU 8796 skeleton. It now targets
**iOS 26.5.2 (23F84, Darwin 25.5.0, XNU 12377.121.6, arm64e)**.

Nothing here has booted. Nothing here has even linked XNU 12377. What follows is
what changed, why each change was forced, and what is still a guess. The
guesses are also in `PENDING.json`; this file is the reasoning behind them.

## The single fact that decided the approach

UniXNU upstream already ships everything an arm64e iOS 26 target needs:

| Piece | Where | State |
| --- | --- | --- |
| `profiles/arch/arm64e/` | UniXNU | 4 KiB pages, 68-patch series against xnu-12377, PAC keys |
| `patches/xnu-12377/` | UniXNU | the series itself, including `native-pac-keys.patch`, `native-pac-arm64-user-jop.patch` |
| `profiles/os/ipados-26.5.2-23F84/` | UniXNU | the OS profile for this exact kernel |
| `patches/xnu-12377/` | UniXNU-sm8550-common | the SoC layer |
| a working arm64e iOS 26 board | UniXNU-sheng | `sheng_ipados26_defconfig`, same profile |

That last row is the important one. It means the arm64e series is not a pile of
untested patches: one board in the ecosystem already pairs this profile with
this SoC family and builds. The migration is therefore **reuse the arch layer,
do not port the old one** — not "rebase 65 hunks onto a new XNU".

The alternative — porting the public `hakios-arm64e-dada` tree, which is the iOS
26 port to a Snapdragon 8 Elite phone — was rejected for two concrete reasons,
both recorded in that project's own issue tracker:

- The system-image tooling is not published. `ios26/fetch.sh`,
  `ios26/select-system.py` and `prebuilt/userland` do not exist in the tree, so
  `userland/build.sh` stops at `$IOS/system.manifest` and falls back to a
  PREBUILT directory that is not there. The author has not answered.
  ([issue #4](https://github.com/superboyyy/hakios-arm64e-dada/issues/4))
- Its own kernel PAC-panics before user space in QEMU on a clean build
  (`PAC failure from kernel with IA key while branching to x22`, inside
  `IOCommandGate::runAction` at a `blraaz x22`), and the reporter's evidence
  points at data pointers being compiled unsigned. That is the same failure mode
  the arm64e series exists to prevent, and UniXNU's version is the one with a
  board that boots.

So this port borrows the *approach* from dada — arm64e natively, Metal through a
translation layer, iOS 26 user space — and takes the *code* from UniXNU.

## What changed

### Verified by running the driver, 2026-11-16

The configuration chain was resolved end to end on a host using UniXNU `main`
(the local checkout in this workspace was older and does not support
`kind = "soc"`, `profiles/`, or `arch:` prefixes). What actually ran:

| Command | Result |
| --- | --- |
| `unixnu status --json` | rc 0, 72,248 bytes of resolved JSON |
| `unixnu defconfig myron_ios26_defconfig` | rc 0, wrote `.config` |
| `unixnu status --json` (with all modules local) | rc 0, 18 modules, patch order 72 |
| `unixnu tree` | rc 0, tree assembled |

What that proves, and what it does not:

- **Proved:** the four profiles load (`arch`, `soc`, `os`, `device`); the OS
  profile's `arch = "arm64e"` selects `profiles/arch/arm64e`; 11 components
  resolve from the catalog; the patch order resolves to 72 files — 70 from the
  shared arm64e/arch and OS series, 2 from the SoC series, 0 from the device
  series; the merged `.config` parses to 20 distinct switches with the intended
  effective values; and the build tree assembles with 9 drivers, 3 kits
  (including the device package as `kits/myron`), 5 patch roots and 62 scripts.
- **Not proved:** nothing compiled. `build kernel` is Linux/macOS only — it
  refuses to start on Windows, and on Linux it needs the Xcode-derived toolchain
  from `scripts/linux/setup-toolchain.py` plus a 23F84 kernelcache. Running it
  is the next step, and it needs a Linux host or CI, not this machine.

Note on how the tree was assembled here: `unixnu tree` creates the tree out of
symlinks, and Windows without `SeCreateSymbolicLink` cannot make them. A throwaway
copy of the driver in this workspace was patched to fall back to directory
junctions (`mklink /J`) and hard links (`mklink /H`). **That patch is not part of
this repository and is not proposed as a change to UniXNU** — it exists only so
the resolution steps above could be exercised on this host. Upstream's symlink
design is correct for its own targets.

Two more defects surfaced during that work, neither of them in this repository:
`unixnu tree`'s post-condition tests `(tree/'package').is_symlink()`, which is
false for a junction, and `_link` cannot remove a junction with `unlink`. Both
are consequences of the same Windows limitation.

### CI

`.github/workflows/build.yml` was retargeted so the same resolution runs on a
Linux runner. No job touches the device, writes a partition, or needs Apple's
image.

| Job | What it now does for this board |
| --- | --- |
| `validate` | unchanged mechanics; `porting.toml` identity retargeted to match `board.toml` so `check_identity` passes |
| `device-tree` | compiles `dts/myron.dts` and `dts/myron-handoff.dts`, asserts the handoff tree carries `unixnu,adt-name`, GICv3 and UFS |
| `resolve` | installs the OS profile, fetches, defconfigs, assembles the tree, prints the resolved profiles/content/patch order, checks the merged `.config`, checks the patch order |
| `partition-safety` | unchanged; it only exercises the UniXNU dual-system tooling and never touches a board |
| `kernel` | targets `xnu-12377` and `myron_ios26_defconfig`; treats the missing 23F84 image as the expected stop |

Four checks were rewritten rather than renamed, because the iOS 16 versions
asserted things that are no longer true:

1. **The merged-config list expected `XNU_NATIVE_AES=y`.** No fragment on this
   target sets it — `arm64e/configs/common.config` has seven switches and none is
   AES, and `sm8550.config` is one line (`NATIVE_ADRENO=y`). The inherited check
   would have failed for a switch nothing sets. AES arrives through the
   `SoftwareAES` component, not a config choice. Removed.
2. **It also expected `XNU_NATIVE_PAC`** in the earlier draft of this migration.
   PAC is unconditional in `native-pac-keys.patch`; there is no switch. Replaced
   with the switches that actually resolve, and `NATIVE_PAN` added because the
   arch fragment really does set it.
3. **The patch-order check asserted the resolved order equals
   `patches/xnu/series`.** That was true when one file was the whole order. It is
   now four series concatenated, and `patches/xnu/series` is the retired iOS 16
   rebase. The check now asserts the shape (first `bcm2837-machine-config.patch`,
   last `zzz-boot-progress.patch`), the load-bearing patches, `>= 60` shared
   entries and exactly `2` SoC entries, and that nothing resolves out of the
   retired `patches/xnu/` directory.
4. **An earlier attempt detected the retired series by file name and was
   wrong.** The two series share about 50 names (`native-arm-pan.patch` and
   friends) with different contents — the device copies are rebased onto
   xnu-8796, the shared ones onto xnu-12377. Comparing names would either miss a
   real double-application or flag a harmless collision, so detection is by the
   directory each entry resolves from.

`unixnu.lock` carried the xnu-8796 commit. It now carries the `xnu_revision` the
OS profile pins (`ac9718fb…`), which is what `unixnu fetch` would write. The
other lock entries are kept: they are UniXNU's global feed pins and pruning them
here would only invite drift.

Every expected value in the merged-config check was re-derived from the fragment
that actually supplies it and then verified with `scripts/check-merged-config.py`
against a config assembled from the real fragments: 16 values match.

Two defects were found by running it, both now fixed:

1. **`soc:patches/xnu-12377/series` does not exist.** The pinned commit
   `23b2049fcd9e` of `UniXNU-sm8550-common` has `patches/xnu/series` holding
   `native-smp-secondary.patch` and `sheng-platform-power.patch`, and no
   `xnu-12377` directory at all. `UniXNU-sheng`'s `board.toml` names the
   `xnu-12377` path, so copying that line would have failed at patch
   resolution. `board.toml` now names `soc:patches/xnu/series`.
2. **`CONFIG_XNU_NATIVE_PAC` does not exist.** An earlier draft of
   `configs/myron_ios26_defconfig` claimed the arm64e arch fragment supplied it.
   It does not: `profiles/arch/arm64e/configs/common.config` sets only
   `BUILD_ARCHIVES`, `SOURCE_CORECRYPTO14`, `SOURCE_PTHREAD`,
   `ARM_DT_PLATFORM`, `ARMCPU`, `NATIVE_ARCH_TIMER` and `NATIVE_PAN`. PAC comes
   in through `native-pac-keys.patch` and `native-pac-arm64-user-jop.patch`,
   which are unconditional. The comment is corrected and nothing is set for it.
3. **Duplicate switches in `.config`.** `cmd_defconfig` concatenates fragments
   and does not deduplicate; `parse_config` takes the last value. An earlier
   draft set `XNU_BUILD_ARCHIVES=y` twice. Removed. `XNU_NATIVE_ADRENO` still
   appears twice by design — the device line overrides the SoC's `=y` with `=n`
   — and is now commented as such.

### `board.toml`

| Field | Was | Now | Why forced |
| --- | --- | --- | --- |
| `os_profile` | `ios-16.7.16-20H392` | `ios-26.5.2-23F84` | the target |
| `[kernel] defconfig` | `myron_ios16_defconfig` | `myron_ios26_defconfig` | the flags differ |
| `[kernel] patch_series` | `["device:patches/xnu/series"]` | `arch:` + `soc:` + `os:` + `device:` | the shared arm64e series is the order; the device-local one is not |
| `[components] include` | 9 entries | +`RamBlock`, `AppleKeyStoreState` | the arm64e OS profile selects both |
| `[payload] title` | `myron iOS 16.7.16 bring-up` | `myron iOS 26.5.2 bring-up` | label |
| `[firmware.defines] BUILDID_STRING` | `myron-ios16-research` | `myron-ios26-research` | label |
| `[payload.adt] chip_id` | `"207"` | `"8142"` | 0x8142 is iPad17,3; 0x207 was iPhone X |
| `[payload.adt] ipad_identity` | `false` | `true` | the profile forges an iPad |
| `[payload.adt] root_match_volume_uuid` | `DADD93F8-…` | `546DFF4A-…` | the old UUID belongs to the 20H392 image |
| `[kernel.prelink]` comments | pointed at the iOS 16 profile | point at the iOS 26 one | accuracy |

Unchanged on purpose: `[userdata]` geometry (the unixnu partition carved
2026-11-15 is still there), `[firmware]` (the published `SILICIUM_UEFI.fd` does
not depend on the OS profile), `[bootimg.image]` header version 0, `[device_tree]`
source, `[payload] dram_size` and `panic_screen = false`.

### `os-profiles/ios-26.5.2-23F84/`

New. Modelled on `profiles/os/ipados-26.5.2-23F84/profile.toml`, with the
`["os"] name` and the `patch_series` path adjusted to live in a device
repository rather than in `profiles/os/`.

Its `patches/xnu/series` has **one** entry, `zzz-boot-progress.patch`, exactly as
the upstream iPadOS 26 profile does. That is not laziness: the 68 arm64e patches
already come in through `arch:`, and repeating them here would apply them twice.

### `configs/myron_ios26_defconfig`

New, derived from the old one with four substantive changes:

- **No `CONFIG_XNU_CRYPTO_BOOTSTRAP` override.** The profile sets `prelinked`;
  `sheng_ipados26_defconfig` on the same profile sets `source`. Both anchors
  exist as patches, so either compiles, and which one this board needs is a boot
  observation nobody has. The inherited default is recorded as such instead of
  being silently replaced by a copy of sheng's choice.
- **`CONFIG_XNU_KERNEL_CHAINED_FIXUPS=y`.** iOS 26 binaries carry chained
  fixups; without it the prelinked kexts and user images cannot be rebound.
- **`CONFIG_XNU_NATIVE_CS_HASH_CACHE` removed.** That switch does not exist in
  the 12377 tree — the work lives in `native-4k-signature-hash-cache.patch`
  instead.
- **`CONFIG_XNU_BUILD_ARCHIVES=y`** restated at board level, because the archive
  list below it is board-specific.

Carried over unchanged: `DT_PANIC_REGION`, `TRUST_CACHE_RUNTIME`,
`NATIVE_UFS`, `NATIVE_VIRTIO_BLOCK`, the `EXTRA_LD_ARCHIVES` pair, and all six
"off" decisions (Adreno, ArmVirt, RamBlock, PMGR, the two display switches).

### `patches/xnu26/series`

New and **empty**. Explained in the file and in `PENDING.json`: the 65 hunks in
`patches/xnu/series` are rebased onto xnu-8796 for iOS 16, the shared arm64e
series covers the same ground against xnu-12377, and applying both applies the
same changes twice. The old series is left in the tree, unreferenced.

### `PENDING.json`, `package/unixnu.toml`

Rewritten. `PENDING.json` keeps the VERIFIED entries that this migration does not
invalidate (device tree, firmware volume) and adds an entry per open question,
each with the evidence level it actually has. `unixnu.toml` drops the NX769J
menu title.

## Identity is not a free parameter

The forged identity moved from **iPhone X (iPhone10,3 / D22AP)** to **iPad17,3
(J820AP)**. That is a regression in fidelity — this is a phone, and an iPad
identity means iPad layouts and iPad product keys — and it is still the right
call, for one reason:

`[kernel.prelink]` pins three values that are properties of **one specific
kernelcache**, not of the board:

- `kernelcache_sha256` — the build refuses to prelink against any other image;
- `early_security_init` — an address inside `AppleMobileFileIntegrity`'s
  `__TEXT_EXEC` in that image;
- `roots` — 30 bundle identifiers that must all be present in that image's
  `__PRELINK_INFO`.

There is exactly one arm64e iOS 26 kernelcache in this ecosystem whose three
values are published and verified: the iPadOS 26.5.2 one. An iPhone 26.x
kernelcache would mean downloading roughly 11 GB and reversing the AMFI
initializer before a single honest value could be written.

Two consequences worth stating plainly:

1. Landing a phone identity later is **a new profile with its own measured
   pins**, not an edit to this one. Copying `early_security_init` between builds
   is the mistake the iOS 16 profile's comments warn about at length.
2. `chip_id` and `ipad_identity` had to move with the model. `devicetree.py`
   parses `chip_id` as hex with no cross-check against the profile, so a stale
   `"207"` would have failed silently at lockdown as "a software update is
   required".

## What to expect when the build is attempted

Ordered by how likely each is to be the first wall:

1. **`kernelcache_sha256` mismatch.** The pin is inherited, not measured. The
   build fails closed with a clear message. Fix:
   `scripts/extract-ipsw-pins.py` against a local 23F84 image.
2. **`roots` mismatch.** Any bundle identifier absent from the image fails
   prelink. Self-correcting once an image is present — the error names the root.
3. **Storage bindings.** `scripts/prelink-storage-bindings.json` holds 190 slots
   measured out of the **20H392** kernelcache. 23F84 has its own layout. This
   fails at link or at first storage access, not at build time, which makes it
   the most expensive of the three to diagnose.
4. **The device patch series being empty.** If myron needs something the arm64e
   series does not carry, it will surface as a runtime panic or a missing symbol,
   not a patch failure. The honest way to find out is to diff
   `patches/xnu/series` against `patches/xnu-12377/` and port any hunk that has
   no counterpart.

None of these is a reason not to have done the migration; all of them are
reasons the status columns in `porting.toml` must stay where they are.

## What was deliberately not done

- **No GPU.** dada reaches SpringBoard with Metal through metal2vulkan and
  Turnip. None of that pipeline is in this repository, and the arm64e profiles
  that do have it (`Adreno740` + `AdrenoMetal`) target Adreno 740 on SM8550,
  not the Adreno 840 here. `CONFIG_XNU_NATIVE_ADRENO=n` stays.
- **No `porting.toml` rewrite.** Its areas still describe the iOS 16 work. They
  are not wrong about hardware — the SoC, the partition geometry and the device
  tree are unchanged — but their kernel and payload narratives are now stale.
  Rewriting 85 KB of area records without a build to point at would be
  documentation about nothing.
- **No attempt to reconcile the two patch series.** Diffing 65 hunks against 68
  is real work with a real answer, and doing it blind (without a build that
  fails on a specific gap) risks removing coverage rather than adding it.
- **No claim that this boots.** The README's status table is left honest.
