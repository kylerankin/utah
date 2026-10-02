#!/usr/bin/bash

# shellcheck source=/dev/null
source /usr/lib/ublue/setup-services/libsetup.sh

# Match the check/commit migration in #259, including older common images.
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
	# firefox-config is optional; skip the default-preferences copy when it is
	# absent rather than failing the first-boot hook on a glob that matches nothing.
	if compgen -G "/usr/share/ublue-os/firefox-config/*" >/dev/null 2>&1; then
		/usr/bin/cp -rf "/usr/share/ublue-os/firefox-config/"* "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref/"
	else
		echo "firefox-config not present; skipping Firefox default preferences"
	fi
fi

version-script-commit flatpaks privileged 1
