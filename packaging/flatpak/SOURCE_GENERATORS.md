# Flatpak source generators

`cargo-sources.json` and `npm-sources.json` are release inputs, generated from
`src-tauri/Cargo.lock` and `frontend/package-lock.json`. They use commit
`737c0085912f9f7dabf9341d4608e2a77a51a73a` of the official
`flatpak/flatpak-builder-tools` repository.

Run `python3 packaging/flatpak/prepare-sources.py` in the controlled online
release-preparation environment. The release build itself must use
`flatpak-builder --disable-download`; Node and Rust remain build-only SDK
extensions and are absent from the installed application.

Accelerator inputs are deliberately maintained outside the core wheelhouse.
`accelerator-sources.json` is the reviewable lock for the optional NVIDIA CUDA
and AMD ROCm extension manifests. Prepare and cache those extension sources in
their own signed refs; never merge their libraries into the CPU application.
The NVIDIA lock records exact wheel hashes, while the ROCm lock records exact
git commits for CTranslate2 and each user-space library. This keeps CUDA and
HIP CTranslate2 builds mutually exclusive and makes `--disable-download`
reproducible for both extension builds.
