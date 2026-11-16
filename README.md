# myron - iOS 26 on the Redmi K90 Pro Max

UniXNU device repository for the **Redmi K90 Pro Max** (codename **myron**), on
the Qualcomm **SM8850 "Canoe"** (Snapdragon 8 Elite Gen 5), targeting
**iOS 26.5.2 (23F84, Darwin 25.5.0, XNU 12377.121.6, arm64e)**.

UniXNU boots iOS and macOS on non-Apple ARM64 hardware through UEFI. This
repository describes one board: its hardware values, component selection,
firmware and partition policy. Shared code lives in
[UniXNU](https://github.com/UniXNU/UniXNU) and in the component repositories.

| | |
| --- | --- |
| Board | Redmi K90 Pro Max |
| Codename | `myron` |
| SoC | Qualcomm SM8850 "Canoe", Adreno 840, `qcom,chipid=0x44050A01` |
| OS profile | `ios-26.5.2-23F84` - iOS 26.5.2, Darwin 25.5.0, XNU 12377.121.6, arm64e |
| Identity | iPad17,3 (`iPad17,3` / J820AP) forged by the ADT - see the caveat below |
| Display | 1200x2608, stride 4800 |
| Touch | FocalTech FT3683, QUP4 SE0, `spi19.0` |
| Storage | UFS, `ufshc@1d84000`, userdata `sda34` |
| Boot | UEFI from `fastboot boot`, boot header version 0, 4096-byte pages |
| Porting record | [`porting.toml`](porting.toml) |

## Status

**Nothing has booted, and nothing has linked XNU 12377 for this board.**

What has been proved is that the configuration resolves: the `Build` workflow on
GitHub Actions passes its `validate`, `device-tree`, `resolve` and
`partition-safety` jobs, and the separate `Porting record` workflow passes.
`resolve` installs the OS profile, fetches every pinned source, merges the four
fragments and asserts the 16 expected switches, the link-line archives, the
72-entry patch order and the source pins. The `Build the kernel` job runs but has
not reported yet; it rebuilds clang from source first. See `MIGRATION.md` for the
six defects those runs found.

This repository was an iOS 16.7.16 / XNU 8796 skeleton. It was migrated to the
arm64e iOS 26 target; [`MIGRATION.md`](MIGRATION.md) records every field that
changed, the evidence behind it, and what is still a guess. Read that file before
trusting any status claim here or in `porting.toml`.

| Milestone | State |
| --- | --- |
| Board description, partition policy, device tree | carried over from the iOS 16 work, still accurate |
| OS profile for 23F84 / arm64e | written |
| Patch order resolved against the shared arm64e series | **done** on CI: 72 entries, no iOS 16 rebase |
| Kernel compiles and links | **not attempted** on this target |
| Prelink | **not attempted**; the kernelcache pin is inherited, not measured |
| UEFI firmware volume | **built** (see below); it does not depend on the OS profile |
| Payload, userdata, boot image | **not attempted** on this target |
| Boot on hardware | not attempted |

The only thing in this table with a hash behind it is the firmware volume:
`myron-uefi` run 43711284872 published `SILICIUM_UEFI.fd`, 8,388,608 bytes,
sha256 `6dc6760fd9fb0a5e27e3ad7056015d82c8d07686bf8815bb529719e922651f59`.

### Why the identity is an iPad on a phone

`[kernel.prelink]` pins three values that belong to one specific kernelcache:
`kernelcache_sha256`, `early_security_init` and the 30-entry `roots` list. Only
one arm64e iOS 26 kernelcache in the ecosystem has all three published and
verified, and it is the iPadOS 26.5.2 one. An iPhone identity would mean
downloading roughly 11 GB and reversing AMFI's initializer before a single
honest value could be written.

That is a fidelity regression, not a design choice, and it is recorded as such
in `PENDING.json`. Landing a phone identity later is a new profile with its own
measured pins, not an edit to this one.

## Expected first failures

Listed by likelihood, from `MIGRATION.md`:

1. `kernelcache_sha256` mismatch - the pin is inherited from the iPadOS 26
   profile, not measured here. The build fails closed with a clear message.
2. `roots` mismatch - any bundle identifier absent from the image fails prelink.
3. Storage bindings - `scripts/prelink-storage-bindings.json` holds 190
   IOBlockStorageDevice slots measured out of the **20H392** kernelcache. They
   have not been re-measured for 23F84, so this fails at link or at first storage
   access rather than at build time.
4. The device patch series is empty - if myron needs something the shared arm64e
   series does not carry, it appears as a runtime panic, not a patch failure.

## The partition this board writes to

The OS windows go to a dedicated `unixnu` GPT entry carved out of the tail of
`userdata` on 2026-11-15, so Android's own `/data` is untouched:

```
userdata   LBA 0x39f200 .. 0x5AAEAFF   348.243 GiB
unixnu     LBA 0x5AAEB00 .. 0x7AAEAFF   128.000 GiB   (unformatted)
```

The carve is reversible and was rolled back and re-applied three times during
bring-up. The procedure lives with the build driver, not in this repository:
`scripts/dual_sys/F2FS-RESIZE-RUNBOOK.md` in the `unixnu-myron-build` checkout.
It documents the gates, the write order and the ten failure modes that were
actually hit; read it before touching the partition table. That same checkout
holds the dual-system tooling (`f2fs-shrink`, `partition.py`, `twrp.sh`), which
is why none of it appears here.

Nothing in this repository formats a partition, and `/data` must never be
formatted: restoring the GPT cannot undo an F2FS relocation, and a full recovery
needs the original userdata capacity *and* the matching encrypted metadata
backup.

## Inputs no repository can hold

| Input | Where it comes from |
| --- | --- |
| 23F84 restore image | Apple's CDN; about 11 GB; not redistributable |
| 56 vendor UEFI DXE drivers | this unit's own `xbl`/`uefi` image, via `scripts/extract-uefi-drivers.py` |
| Firmware DTB | `vendor_boot`, via `scripts/extract-firmware-dtb.py` |
| Unique chip ID | read from the device, passed as `--unique-chip-id` |
| OS trust cache | one of the two the profile pins, passed as `--trust-cache` |
| Root volume UUID | `scripts/extract-root-volume-uuid.py` on the built userdata image |

## Building

From a UniXNU checkout:

```sh
./unixnu defconfig myron_ios26_defconfig --device ../myron-ios26
./unixnu status  --device ../myron-ios26 --json
./unixnu tree    --device ../myron-ios26
./unixnu build kernel --device ../myron-ios26
```

The firmware volume builds separately, from the `myron-uefi` repository, and its
output is what `[firmware] fd` names.

## Related repositories

| Repository | Contents |
| --- | --- |
| [UniXNU](https://github.com/UniXNU/UniXNU) | build driver, UEFI boot manager, shared profiles and patches |
| [UniXNU-sm8550-common](https://github.com/UniXNU/UniXNU-sm8550-common) | the pinned SoC source and its `patches/xnu-12377/` |
| `myron-uefi` | the Mu-Silicium platform package this board boots |

## License

The repository's own text and scripts are provided as-is for research. Shared
components keep the licences of their upstream projects; see each component.

Apple, iOS and Metal are trademarks of Apple Inc. This project is not affiliated
with Apple, Qualcomm or Xiaomi.
