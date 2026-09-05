# On-device pieces that are not part of the ROM build

Everything here is installed once and survives dirty flashes. None of it is
distributed in this repo; the hashes are what was verified working on
2026-08-30 / 2026-09-05 so a re-download can be checked.

## Root: KernelSU-Next Manager

The kernel is `dev-susfs` (version `30000+n`, 33252 at the pinned commit). The
**official** Manager APK accepts it; the tutorial's `next-susfs` branch would
not (it numbers itself `10000+n+200` and every current Manager rejects it).

| file | sha256 |
|---|---|
| `KernelSU_Next_v3.3.0_33214-release.apk` | `fd0b12385c98fe9d5f4f1257b5f184e55c74c1376637507df0718305f5d7a924` |
| `KernelSU_Next_v3.3.0-spoofed_33214-release.apk` (random package name, used on device) | `773d99e256563d36f8543235c96274021c59cf65efed783170bbc7effdf24ee3` |

The spoofed Manager does not self-update and each release has a different
random package name: install the new one, then uninstall the old.

## Play Integrity stack (install in this order)

| module | id | version | sha256 | role |
|---|---|---|---|---|
| SUSFS module | `susfs4ksu` | v2.3.0 | `c4adfa803b3a7c107d47d983db762ab122f17285c35049786b38db1444b22427` | root/mount hiding (the kernel half is in the ROM) |
| NeoZygisk (JingMatrix) | `zygisksu` | v2.4 (289) | `93a1425c67bb89f58a0dcb9fc823ec93f6472214f221e667a70b67c6a6e061a1` | Zygisk provider - KSU-Next ships none |
| AlwaysStrong (evoker0) | `tricky_store` | v1.0.3 | `6f669a7f4dd438df3ede42c001cf24b24e78b42e85ed21f78fc842d25737a774` | TEE-Simulator-RS + PlayIntegrityFork |

Result on device: **MEETS_STRONG_INTEGRITY**. NeoZygisk prints "KernelSU
version too large!" on install (its gate is `MAX_KSU_VERSION=30000`) and
installs anyway - warning, not abort. NoMount was deliberately skipped: it
duplicates SUSFS and its authors label it experimental.

## GApps

`NikGapps-crdroid-official-arm64-16-20251128`, sha256
`f7883e80dc900aa7b7e7f4de7d9be775a938cba5f262f3c39b9413ce4b7df718`,
sideloaded in the same recovery session as the ROM. The self-built recovery
does not verify zip signatures at all (`verify_package()` is commented out in
`bootable/recovery`), which is why the unsigned NikGapps zip installs.

## The camera

`opluscam_waffle.zip`, built by the `oplus-camera-waffle` repo from an
OxygenOS 16.0.8 OTA dump. Its `customize.sh` refuses to install unless the
running ROM carries `ro.oplus.cam.treepatch=1`, which the tree edits applied by
`scripts/camera-apply.sh` set. Rebuild the module only when the ROM's vendor
blob set or platform key changes; a ROM update alone does not require it.

## What must be re-applied on every source update, and what must not

| survives a no-format sideload | must be re-applied before each build |
|---|---|
| all `/data/adb/modules`, keybox, pif.prop, app data, root grants | ARB0 firmware swap (`scripts/arb0-reapply.sh`) |
| the camera module and its config | KSU-Next + SUSFS (`scripts/ksu-reapply.sh`) |
| | camera tree edits (`scripts/camera-apply.sh`) |

Root hiding is entirely module-side and survives - *provided the new kernel
still has KSU+SUSFS*. A rebuild without it makes the modules load against a
plain kernel and root silently breaks. That is what `scripts/preflight.sh`
exists to catch.
