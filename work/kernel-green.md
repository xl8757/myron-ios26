# Kernel build reached green - 2026-11-17

Run 44038495686, commit 6a7b2e114b. All five jobs succeed and the kernel job
prints "kernel build passed".

## The artifact

    kernel/kernel.macho
    bytes   89423872  (85.3 MiB)
    sha256  68bcd2fbb0f9ec43a5f92be1092cdc025f4fc8b670f8410257576e1ae7eb736f

Built as arm64e for iOS 26.5.2 (23F84), XNU 12377.121.6, from Apple's source
with the 71-patch arm64e series, then prelinked against the 23F84 kernelcache.

## The chain that produced it

| Stage | Result |
|---|---|
| Toolchain with the ptrauth patch | pass |
| 71 patches applied | pass |
| bootstrap pass, 942 compile records | pass |
| 16 archives | pass |
| kernel compile | pass |
| kernel link | pass |
| FIPS POST seal | pass |
| prelink with --allow-missing | pass, 89423872 bytes |

## Prelink numbers

    original   21113 functions, 2885 diversities
    native     29411 functions,  841 diversities
    paired      6805/21113  (32%)
    unresolved closure references   808
    stubbed functions              1896
    reviewed anchors 108, string anchors 3572, PAC shapes 333, vtables 980

The 16306 in the Incomplete-pairing message is the function-mapping deficit;
808 is what remains unbound in the emitted closure. String anchors, PAC shapes,
pure leaves and vtables carry most references even where a function was not
matched by hash.

## What this means, honestly

The kernel is real: compiled, linked, sealed, prelinked and written. It is not
a working system. 808 unresolved references and 1896 stubs are expected to
fail a boot at the first rebound kext, and nothing has been run on hardware.

## Next

kernel.macho.json enumerates every gap by origin and target - the roadmap for
either a RELEASE-LTO native build or more reviewed anchors.
