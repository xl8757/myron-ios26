# Agent instructions

This repository is the myron board configuration for the **Redmi K90 Pro Max**
(SM8850 / Canoe, codename `myron`): hardware description, component selection,
firmware and partition policy, and packaging. It was derived from
`UniXNU-NX769J` and still carries that board's text in places; where the two
disagree, this file and `board.toml` win.

Shared SM8550 resources and operations belong to `UniXNU-sm8550-common`. The
documentation site, component catalog and common build tools belong to `UniXNU`.

Read the following before editing:

| Task | Required reading |
| --- | --- |
| Every port | [Porting](https://unixnu.com/porting/), [repository format](https://unixnu.com/porting/device-repository), [component catalog](https://unixnu.com/reference/components) |
| Hardware | [Device trees](https://unixnu.com/porting/device-tree), [matching](https://unixnu.com/drivers/device-tree-matching), the pinned SoC README and `sm8550.dtsi` |
| Components | Selected component README, catalog metadata, build interface and relevant source |
| Kernel | Selected OS profile, XNU source, [patch reference](https://unixnu.com/xnu/source-and-patches), ordered patch series |
| Images | [Prelink](https://unixnu.com/concepts/prelink), [payload](https://unixnu.com/concepts/payload), [UEFI firmware](https://unixnu.com/porting/uefi-firmware) |
| Verification | [Building](https://unixnu.com/building), [bring-up](https://unixnu.com/porting/bring-up) |
| Migration | [Migration guide](https://unixnu.com/porting/migration) |

Search UniXNU's `components.toml` and read existing implementations before
adding a capability. Select components by interface, hardware scope, OS ABI and
board dependencies. Use canonical component names in sources, paths, build
commands and dependencies. Preserve source filenames, runtime classes, bundle
IDs, plugin filenames, symbols and protocol IDs.

Use `dts/myron-handoff.dts` for the ADT handoff tree; `dts/myron.dts` stays as
the byte-exact stock reference and is not the `[device_tree] source`. Inspect
`local.toml` and CLI overrides before changing source pins.

Run from the UniXNU checkout:

```sh
./unixnu defconfig myron_ios26_defconfig --device ../myron-ios26
./unixnu status --device ../myron-ios26 --json
./unixnu tree --device ../myron-ios26
./unixnu build kernel --device ../myron-ios26
```

Use the README for firmware and payload inputs. Run existing checks relevant to
the change. Report commands, outputs, missing inputs and hardware observations
separately. Record device boot, storage, display, input and GPU checkpoints.

Write concise English for this repository. Describe current behavior with direct
statements and ordinary punctuation.

## Board facts that are easy to get wrong

- The target is **iOS 26.5.2 (build 23F84, Darwin 25.5.0, XNU 12377.121.6,
  arm64e)** with an **iPad17,3 / J820AP** identity. The profile is
  `os-profiles/ios-26.5.2-23F84/` and has to land in `UniXNU/profiles/os/`
  before a build resolves. `MIGRATION.md` explains why the identity is iPad and
  why that is not a free parameter: `[kernel.prelink]` pins
  `kernelcache_sha256`, `early_security_init` and `roots`, all three read out of
  one specific kernelcache. Never copy those values between profiles.
- The arch layer is `arm64e`, not `arm64`. That single field in the OS profile
  selects `profiles/arch/arm64e` and with it its 69-entry series against
  xnu-12377. Note what it does *not* do: there is no `CONFIG_XNU_NATIVE_PAC`
  switch. Pointer authentication comes in through
  `patches/xnu-12377/native-pac-keys.patch` and
  `native-pac-arm64-user-jop.patch`, which are unconditional. A kernel built
  without the arm64e series panics on the first authenticated indirect branch;
  that is the failure the public dada port reports.
- The patch order is the four shared series - `arch:`, `soc:`, `os:`, `device:`.
  The SoC series is `patches/xnu/series` inside `UniXNU-sm8550-common`, **not**
  `patches/xnu-12377/`: at the pinned commit `23b2049fcd9e` that directory does
  not exist. `UniXNU-sheng`'s board.toml names the `xnu-12377` path and would
  fail to resolve against the same pin. The SoC series holds exactly two
  patches, `native-smp-secondary.patch` and `sheng-platform-power.patch`.
- `patches/xnu/series` (65 hunks, rebased onto xnu-8796 for the old iOS 16
  target) is **not** part of that order, and referencing it would apply the same
  changes twice. `patches/xnu26/series` is the device series and is empty. Read
  `MIGRATION.md` before adding anything to it.
- The SoC source is still `UniXNU-sm8550-common`. Its register map matches
  SM8650 for every resource this port drives, but its CPU core names and Adreno
  generation do not, and no `UniXNU-sm8850-common` exists. Keep the per-node
  overrides in `dts/` and `configs/myron_ios26_defconfig`.
- `soc:configs/sm8550.config` sets `CONFIG_XNU_NATIVE_ADRENO=y`. The device
  defconfig is applied last and turns it off. Do not drop that line: this board
  has an Adreno 840 and no qualified driver.
- `scripts/prelink-storage-bindings.json` holds 190 slots measured out of the
  **20H392** kernelcache. They have not been re-measured for 23F84, so a storage
  failure on this target is expected rather than surprising.
- The boot image is header version 0, which is what `unixnu` can pack and
  verify. The NX769J board files record version 4; that is the Android build
  system's setting for a stock boot.img, not this tool's.
- Nothing in this port is device verified, and nothing has linked XNU 12377 for
  this board. Do not raise an area's status without the evidence its level
  requires.

## Porting record

Read `porting.toml` before implementation. Identify the affected areas and their
existing components, then update the implementation and each affected area's
findings or evidence in the same change. Update configuration, procedure and
completion criteria when an interface changes.

Note that `porting.toml` still describes the iOS 16 target in its kernel and
payload areas. The hardware areas are accurate; the kernel narrative is stale
and is tracked in `MIGRATION.md` rather than silently rewritten.

Every area needs the `evidence` key even when it is empty. A changed code path
must be owned by an area's `paths`, and each owning area must gain a new
`findings` or `evidence` entry with that change.

```sh
python3 scripts/porting-record.py check --device ../myron-ios26
python3 scripts/porting-record.py check --device ../myron-ios26 --base origin/main
```
