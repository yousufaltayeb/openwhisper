#!/bin/sh
echo "Archived pre-rewrite Flatpak workflow; refusing to publish an obsolete package" >&2
exit 64

# Build a signed OSTree repository and client metadata from an offline Flatpak
# build. Private key material is supplied by CI secrets only.
set -eu

ow_branch=${1:?usage: publish-flatpak-repo.sh <beta|stable> <build-dir> <output-dir>}
ow_build_dir=${2:?usage: publish-flatpak-repo.sh <beta|stable> <build-dir> <output-dir>}
ow_output_dir=${3:?usage: publish-flatpak-repo.sh <beta|stable> <build-dir> <output-dir>}
ow_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
ow_manifest="$ow_root/packaging/flatpak/io.github.yousufaltayeb.OpenWhisper.yml"
ow_public_key="$ow_root/packaging/flatpak/keys/openwhisper-public.asc"

case "$ow_branch" in
    beta|stable) ;;
    *) printf '%s\n' 'branch must be beta or stable' >&2; exit 2 ;;
esac

: "${OPENWHISPER_GPG_PRIVATE_KEY:?missing GitHub secret OPENWHISPER_GPG_PRIVATE_KEY}"
: "${OPENWHISPER_GPG_PASSPHRASE:?missing GitHub secret OPENWHISPER_GPG_PASSPHRASE}"
: "${OPENWHISPER_GPG_FINGERPRINT:?missing GitHub secret OPENWHISPER_GPG_FINGERPRINT}"
if [ ! -s "$ow_public_key" ]; then
    printf '%s\n' "missing committed public key: $ow_public_key" >&2
    exit 2
fi

ow_gnupg=$(mktemp -d "${TMPDIR:-/tmp}/openwhisper-gpg.XXXXXX")
ow_repo="$ow_output_dir/repo"
cleanup() {
    rm -rf -- "$ow_gnupg"
}
trap cleanup EXIT HUP INT TERM
chmod 700 "$ow_gnupg"
export GNUPGHOME=$ow_gnupg

printf '%s' "$OPENWHISPER_GPG_PRIVATE_KEY" | base64 --decode | gpg --batch --import
# Prime gpg-agent without putting the passphrase in argv or a file.
printf '%s' "$OPENWHISPER_GPG_PASSPHRASE" | gpg --batch --pinentry-mode loopback \
    --passphrase-fd 0 --local-user "$OPENWHISPER_GPG_FINGERPRINT" \
    --detach-sign --output /dev/null /dev/null

# The workflow mirrors the currently hosted repository into this directory
# first. Building directly into it preserves the other release branch (for
# example, publishing beta must not erase the stable ref).
mkdir -p "$ow_repo"
if [ ! -f "$ow_repo/config" ]; then
    ostree --repo="$ow_repo" init --mode=archive-z2
fi
flatpak-builder --force-clean --disable-download --default-branch="$ow_branch" \
    --gpg-sign="$OPENWHISPER_GPG_FINGERPRINT" --gpg-homedir="$GNUPGHOME" \
    --repo="$ow_repo" "$ow_build_dir" "$ow_manifest"
flatpak build-update-repo --generate-static-deltas \
    --gpg-sign="$OPENWHISPER_GPG_FINGERPRINT" --gpg-homedir="$GNUPGHOME" "$ow_repo"

ow_key_base64=$(gpg --export "$OPENWHISPER_GPG_FINGERPRINT" | base64 --wrap=0)
sed \
    -e "s|@OPENWHISPER_GPG_PUBLIC_KEY_BASE64@|$ow_key_base64|g" \
    -e "s|@OPENWHISPER_BRANCH@|$ow_branch|g" \
    "$ow_root/packaging/flatpak/io.github.yousufaltayeb.OpenWhisper.flatpakrepo.in" \
    > "$ow_output_dir/openwhisper-$ow_branch.flatpakrepo"
sed \
    -e "s|@OPENWHISPER_GPG_PUBLIC_KEY_BASE64@|$ow_key_base64|g" \
    -e "s|@OPENWHISPER_BRANCH@|$ow_branch|g" \
    "$ow_root/packaging/flatpak/io.github.yousufaltayeb.OpenWhisper.flatpakref.in" \
    > "$ow_output_dir/openwhisper-$ow_branch.flatpakref"
cp "$ow_root/packaging/linux/io.github.yousufaltayeb.OpenWhisper.svg" \
    "$ow_output_dir/io.github.yousufaltayeb.OpenWhisper.svg"
