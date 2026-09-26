#!/usr/bin/env bash
# Bake Utah's default Flatpaks and the bootc-installer bundle into the live
# squashfs. Adapted from dakota-iso: the cache is build-only; the resulting
# Flatpak repository is part of the ISO and is available offline to fisherman.
#
# Utah's default Flatpaks are declared in flatpak's standard preinstall.d (see
# the generation below) rather than listed here, so the Bluefin parity Brewfile
# stays the single source of truth and any ISO builder that runs `flatpak
# preinstall` bakes the same set. See projectbluefin/utah#257.
set -euo pipefail

FLATPAK_CACHE=/var/cache/flatpak-dl
# The parity contract: Bluefin's Brewfile, shipped by the common profile. The
# preinstall.d entries below are generated from it, so adding a flatpak to the
# Brewfile adds it to every Utah ISO without touching this script.
BREWFILE=/usr/share/ublue-os/homebrew/system-flatpaks.Brewfile
PREINSTALL_DIR=/usr/share/flatpak/preinstall.d
INSTALLER_APP_ID=org.bootcinstaller.Installer
INSTALLER_REPO=projectbluefin/bootc-installer
BUNDLE=org.bootcinstaller.Installer.flatpak
# Pin the installer release so ISO composition is reproducible rather than
# resolving a mutable `latest` during the build. Override with
# UTAH_INSTALLER_VERSION when validating a newer installer.
INSTALLER_VERSION="${UTAH_INSTALLER_VERSION:-v3.0.16}"
# The bundle is installed system-wide with --no-gpg-verify below, so the
# version pin alone is the whole trust story. Pin its SHA-256 the same way
# the Containerfile pins UUPD_SHA256, and verify before import. Version and
# digest move together; override with UTAH_INSTALLER_SHA256 when validating
# a newer installer.
INSTALLER_SHA256="${UTAH_INSTALLER_SHA256:-6d68445965bf03fd628fcc9e856b162939b5f87bf4532f62725cf0e114c7eea7}"

mkdir -p "${FLATPAK_CACHE}/tmp" /run/dbus
export TMPDIR="${FLATPAK_CACHE}/tmp"
# /root is a symlink to /var/roothome in a bootc image and the target does not
# exist during a container build, so mkdir -p /root/... fails outright. Point
# the cache somewhere writable instead; this only silences dconf's warnings.
export XDG_CACHE_HOME="${FLATPAK_CACHE}/cache"
mkdir -p "${XDG_CACHE_HOME}"
# An OCI flatpak remote makes flatpak spawn a session bus of its own, and a bus
# refuses to start without a machine id -- which a container image does not
# have. Flathub installs never needed one; the TunaOS remote does:
#   Cannot spawn a message bus without a machine-id
# The id is build-time only; systemd regenerates a real one on first boot.
if [[ ! -s /etc/machine-id ]]; then
    systemd-machine-id-setup >/dev/null 2>&1 || dbus-uuidgen > /etc/machine-id
fi
dbus-daemon --system --fork --nopidfile
# ...and a session bus. Installing from an OCI remote reaches for one and,
# finding none, tries to autolaunch it:
#   error: Cannot autolaunch D-Bus without X11 $DISPLAY
# Flathub's ostree remotes never ask for it, which is why this only appeared
# when the TunaOS remote was added.
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address)"
    export DBUS_SESSION_BUS_ADDRESS
fi
sleep 1

if [[ -d "${FLATPAK_CACHE}/repo/refs" ]]; then
    # cp, not rsync: rsync is in neither Hummingbird nor the factory, so it
    # cannot be installed into the live layer. -n keeps the seed
    # non-destructive, which is all --ignore-existing was doing.
    cp -a -n "${FLATPAK_CACHE}/repo/." /var/lib/flatpak/repo/ || true
fi

flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
# The Ghostty entry below resolves from the TunaOS remote. The base image ships
# its descriptor at /etc/flatpak/remotes.d, but register it here too so
# `flatpak preinstall` can resolve Ghostty even if the descriptor was not
# auto-imported. Idempotent.
flatpak remote-add --system --if-not-exists tuna-os \
    https://tunaos.org/flatpak/tuna-os.flatpakrepo

# A bundle import needs a temporary local remote in an OCI build: direct
# --bundle installs omit the deploy/active ref without flatpak-system-helper.
# The download comes only from INSTALLER_REPO: an earlier fallback fetched the
# same version tag from tuna-os/tuna-installer, which would have let a release
# in a different org substitute the installer every Utah ISO ships.
curl --retry 3 --fail --location \
    "https://github.com/${INSTALLER_REPO}/releases/download/${INSTALLER_VERSION}/${BUNDLE}" \
    -o /tmp/bootc-installer.flatpak
echo "${INSTALLER_SHA256}  /tmp/bootc-installer.flatpak" | sha256sum --check --strict
local_repo=/tmp/bootc-installer-repo
ostree init --repo="${local_repo}" --mode=archive-z2
flatpak build-import-bundle "${local_repo}" /tmp/bootc-installer.flatpak
rm -f /tmp/bootc-installer.flatpak
flatpak remote-add --system --no-gpg-verify installer-local "file://${local_repo}"
flatpak install --system --noninteractive installer-local "${INSTALLER_APP_ID}"
flatpak remote-delete --system --force installer-local || true
rm -rf "${local_repo}"

# Recreate the active deployment link normally written by flatpak-system-helper.
for branch in /var/lib/flatpak/app/${INSTALLER_APP_ID}/x86_64/*; do
    [[ -d "${branch}" ]] || continue
    if [[ ! -L "${branch}/active" ]]; then
        deployment="$(find "${branch}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | head -1)"
        [[ -n "${deployment}" ]] && ln -sfn "${deployment}" "${branch}/active"
    fi
done
flatpak override --system --filesystem=/etc:ro "${INSTALLER_APP_ID}"

# Declare Utah's default Flatpaks in preinstall.d. flatpak-preinstall.service
# runs `flatpak preinstall -y` on first boot, and tuna-os/tacklebox bakes the
# same set from these entries, so this is the single place the default list
# lives. The entries are generated from the parity Brewfile rather than
# re-listed, so the two can never drift.
mkdir -p "${PREINSTALL_DIR}"
PREINSTALL_BREW="${PREINSTALL_DIR}/brewfile.preinstall"
: > "${PREINSTALL_BREW}"
# Bazaar is declared by the hand-maintained bazaar.preinstall (it carries the
# flathub CollectionID history this repo keeps on purpose), so skip it here to
# avoid declaring the same app-id twice.
while IFS= read -r id; do
    [ -n "${id}" ] || continue
    if [ "${id}" = "io.github.kolunmi.Bazaar" ]; then
        continue
    fi
    # The org.gtk.Gtk3theme.* entries are runtimes on the 3.22 branch, not
    # applications on stable; everything else in the Brewfile is an app.
    if [[ "${id}" == org.gtk.Gtk3theme.* ]]; then
        props=("Branch=3.22" "IsRuntime=true")
    else
        props=("Branch=stable" "IsRuntime=false")
    fi
    {
        printf '[Flatpak Preinstall %s]\n' "${id}"
        printf 'remote=flathub\n'
        for prop in "${props[@]}"; do
            printf '%s\n' "${prop}"
        done
        printf '\n'
    } >> "${PREINSTALL_BREW}"
done < <(awk -F'"' '/^flatpak / && NF >= 2 {print $2}' "${BREWFILE}")

# Ghostty is Utah's only terminal and ships from the TunaOS remote, so it is
# kept out of the Bluefin parity Brewfile (verify-desktop-contract compares it
# byte for byte). Declare it here with its own remote.
printf '[Flatpak Preinstall com.mitchellh.ghostty]\nremote=tuna-os\nBranch=stable\nIsRuntime=false\n\n' \
    > "${PREINSTALL_DIR}/ghostty.preinstall"

# Install everything declared in preinstall.d. This replaces the former
# hand-maintained install list: the entries above are the contract, and running
# preinstall here bakes the same set into the live ISO that the first-boot
# service applies to an installed target.
flatpak preinstall -y

# Pin the listed GTK3 theme runtimes so a later `flatpak uninstall --unused`
# keeps them: nothing depends on them, so --unused would drop them (#256).
# The pin lives in /var/lib/flatpak, which fisherman copies to the target, so
# installed systems keep the themes through later cleanups too.
for runtime in org.gtk.Gtk3theme.adw-gtk3 org.gtk.Gtk3theme.adw-gtk3-dark; do
    flatpak pin --system "runtime/${runtime}/x86_64/3.22" || true
done

mkdir -p "${FLATPAK_CACHE}"
# Replacing the directory outright is what --delete was for: a stale object
# left in the cache would be seeded into the next build and never collected.
rm -rf "${FLATPAK_CACHE}/repo"
mkdir -p "${FLATPAK_CACHE}/repo"
cp -a /var/lib/flatpak/repo/. "${FLATPAK_CACHE}/repo/"
