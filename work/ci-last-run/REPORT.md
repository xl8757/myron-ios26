# CI run 43885816545 — 2026-11-16 22:20 (Asia/Shanghai)

Commit: 58c814d. Run: **failure**, and the failure is one step further along
than the previous run. Full logs are beside this file in `logs/`.

## Where the run got to

| Stage | Result |
|---|---|
| Toolchain build with the ptrauth patch | pass |
| 71 patches applied | pass |
| `Let the archive replay tolerate the target-version override` | pass |
| bootstrap pass | pass, 942 compile records |
| corecrypto archives (both variants) | pass, 529 sources each, 0 failures |
| `component-ArmDTPlatform` compile | pass (the `-Werror` fix worked) |
| `component-ArmDTPlatform` object audit | **fail** |

## The failure

```
ArmDTPlatform: deployment (2, 1114112), expected (2, 1703936)
```

`1114112` is iOS 17.0, `1703936` is iOS 26.0. The object was built for iOS 17
while the kernel target is iOS 26.

## Why

- `scripts/kernel_target.py` derives the target triple and the expected
  deployment from `UNIXNU_XNU_ARCH` and `UNIXNU_IOS_VERSION`, defaulting to
  `arm64` and `17.0` when they are not set.
- `scripts/unixnu/kernel.py` sets both, but only inside `version_env()`, which
  the kernel invocation receives.
- `build_archives()` is handed `xnu_env()` instead, which contains only the
  `CONFIG_XNU_*` to `XNU_*` switches. No profile version reaches the archive
  builders.

So every archive builder silently uses the arm64/17.0 defaults, stamps its
objects for iOS 17.0, and then fails the audit that exists to catch exactly
that.

This is an upstream defect. Nothing in `board.toml` or the defconfig can
influence it, because the value never reaches the script.

## The fix, not yet applied

Pass `version_env()` into `build_archives()` as well, or export
`UNIXNU_XNU_ARCH` and `UNIXNU_IOS_VERSION` for the whole kernel step. Both are
one-line changes in `UniXNU/scripts/unixnu/kernel.py`.

Worth noting: the earlier idea of stripping `-mios-version-min` from the
replayed recipe was rejected at the time, and would have masked this. The
object really was being built for the wrong platform; only the audit said so.

## Progress across runs

Each run has moved the failure later, never back:

1. configuration never resolved
2. patch series resolution
3. lock pins
4. `__ptrauth` parser rejection
5. `__ptrauth` TableGen argument cap
6. `__ptrauth` Sema diagnostic domain
7. virtio/AES/prelinked ordering trap
8. missing component archive entries
9. `-Werror` on a replayed recipe
10. iOS deployment mismatch in the archive builders  ← this run
