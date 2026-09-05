# ARB0 donor blobs

`manifest.sha1` and `manifest.sha256` identify the four OnePlus-signed
bootloader images this build swaps into `vendor/oneplus/waffle/radio/`:
`abl xbl xbl_config xbl_ramdump`, taken as one coherent group from crDroid
16.0 **v12.5** for waffle, the newest official build whose `xbl_config` still
reads anti-rollback 0. The blobs are not committed; `scripts/arb0-donor.sh`
extracts them from that zip (or imports an existing stash) into
`work/arb0-stash/` and refuses anything that does not match these hashes.

SHA-1 is what `vendor/oneplus/waffle/Android.mk` pins per blob
(`add-radio-file-sha1-checked`); SHA-256 is what `docs/ARB.md` and the payload
manifest use. Both are listed so either can be cross-checked.

Why the whole group and not just `xbl_config`: `xbl` and `xbl_config` are a
matched pair (DDR training, clock and PMIC tables), and every firmware image
differs between v12.5 and v12.11. See `docs/ARB.md` sections 5 and 8.
