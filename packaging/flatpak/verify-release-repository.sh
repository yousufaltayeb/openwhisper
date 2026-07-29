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

# ``--installation`` accepts a configured installation name, not a path.
# FLATPAK_USER_DIR gives the client a real, disposable user installation while
# keeping the runner's normal user installation untouched.
ow_flatpak() {
    FLATPAK_USER_DIR="$ow_install" flatpak --user "$@"
}

if ow_flatpak remote-add unsigned "file://$ow_repo" \
    && ow_flatpak remote-ls unsigned >/dev/null 2>&1; then
    printf '%s\n' 'repository was accepted without its public key' >&2
    exit 1
fi

# A prior hosted beta can be installed first, then the same trusted remote is
# redirected to the newly built repository. A clean first release has no prior
# remote and therefore cannot perform this non-bootstrap assertion.
if [ -n "$ow_prior_repo" ]; then
    ow_flatpak remote-add --gpg-import="$ow_public_key" prior "$ow_prior_repo"
    ow_flatpak install --no-deps --noninteractive prior "$ow_app//beta"
    ow_flatpak remote-modify --url="file://$ow_repo" prior
    ow_flatpak update --no-deps --noninteractive "$ow_app//beta"
fi

ow_flatpak remote-add --gpg-import="$ow_public_key" signed "file://$ow_repo"
ow_flatpak install --no-deps --noninteractive signed "$ow_app//$ow_branch"

# New clients prefer the signed summary index and can fall back to the legacy
# summary. Corrupt both entry points so the assertion proves signature failure
# regardless of which metadata format the installed Flatpak version selects.
cp -a "$ow_repo/." "$ow_tampered/"
for ow_metadata in summary summary.idx; do
    if [ -f "$ow_tampered/$ow_metadata" ]; then
        printf '%s\n' tampered >> "$ow_tampered/$ow_metadata"
    fi
done
if ow_flatpak remote-add --gpg-import="$ow_public_key" tampered "file://$ow_tampered" \
    && ow_flatpak remote-ls tampered >/dev/null 2>&1; then
    printf '%s\n' 'tampered repository signature was accepted' >&2
    exit 1
fi
