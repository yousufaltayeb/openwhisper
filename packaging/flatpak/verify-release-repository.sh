#!/bin/sh
# Exercise consumer-side verification after publish. This script intentionally
# uses a disposable Flatpak installation so it never touches a developer's
# configured remotes or installed applications.
set -eu

ow_repo=${1:?usage: verify-release-repository.sh <signed-repo> <public-key> <branch> [prior-repo-url]}
ow_public_key=${2:?usage: verify-release-repository.sh <signed-repo> <public-key> <branch> [prior-repo-url]}
ow_branch=${3:?usage: verify-release-repository.sh <signed-repo> <public-key> <branch> [prior-repo-url]}
ow_prior_repo=${4:-}
ow_app=io.github.yousufaltayeb.OpenWhisper
ow_install=$(mktemp -d "${TMPDIR:-/tmp}/openwhisper-flatpak-test.XXXXXX")
ow_tampered=$(mktemp -d "${TMPDIR:-/tmp}/openwhisper-flatpak-tampered.XXXXXX")
cleanup() {
    rm -rf -- "$ow_install" "$ow_tampered"
}
trap cleanup EXIT HUP INT TERM

if flatpak --installation="$ow_install" remote-add unsigned "file://$ow_repo" \
    && flatpak --installation="$ow_install" remote-ls unsigned >/dev/null 2>&1; then
    printf '%s\n' 'repository was accepted without its public key' >&2
    exit 1
fi

# A prior hosted beta can be installed first, then the same trusted remote is
# redirected to the newly built repository. A clean first release has no prior
# remote and therefore cannot perform this non-bootstrap assertion.
if [ -n "$ow_prior_repo" ]; then
    flatpak --installation="$ow_install" remote-add --gpg-import="$ow_public_key" prior "$ow_prior_repo"
    flatpak --installation="$ow_install" install --no-deps --noninteractive prior "$ow_app//beta"
    flatpak --installation="$ow_install" remote-modify --url="file://$ow_repo" prior
    flatpak --installation="$ow_install" update --no-deps --noninteractive "$ow_app//beta"
fi

flatpak --installation="$ow_install" remote-add --gpg-import="$ow_public_key" signed "file://$ow_repo"
flatpak --installation="$ow_install" install --no-deps --noninteractive signed "$ow_app//$ow_branch"

# The signature is over `summary`; corrupt it after copying to prove client
# verification rejects the altered repository rather than silently trusting it.
cp -a "$ow_repo/." "$ow_tampered/"
printf '%s\n' tampered >> "$ow_tampered/summary"
if flatpak --installation="$ow_install" remote-add --gpg-import="$ow_public_key" tampered "file://$ow_tampered" \
    && flatpak --installation="$ow_install" remote-ls tampered >/dev/null 2>&1; then
    printf '%s\n' 'tampered repository signature was accepted' >&2
    exit 1
fi
