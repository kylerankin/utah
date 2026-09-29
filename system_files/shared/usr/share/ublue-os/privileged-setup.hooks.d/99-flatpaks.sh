#!/usr/bin/bash

# shellcheck source=/dev/null
source /usr/lib/ublue/setup-services/libsetup.sh

# Compat shim: common libsetup.sh builds older than projectbluefin/common #1196
# have only version-script, which records the version before the body runs, and
# no version-script-check/version-script-commit pair. Fall back to that legacy
# gate and make the commit a no-op, so this hook works against both contracts.
if ! declare -F version-script-check >/dev/null; then
    version-script-check() { version-script "$@"; }
    version-script-commit() { :; }
fi

version-script-check flatpaks privileged 1 || exit 0

set -xe

# Set up Firefox default configuration
ARCH=$(arch)
if [ "$ARCH" != "aarch64" ] ; then
	mkdir -p "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref"
	rm -f "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref/*bluefin*.js"
	/usr/bin/cp -rf /usr/share/ublue-os/firefox-config/* "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref/"
fi

# Record success only after the body ran, so a failing first-boot hook retries
# next boot instead of being permanently skipped (common #1196 new contract).
version-script-commit flatpaks privileged 1
