#!/usr/bin/bash

# shellcheck source=/dev/null
source /usr/lib/ublue/setup-services/libsetup.sh

# Bumped to 2 for #489: machines that ran version 1 recorded success while the
# quoted glob made `rm -f` a no-op, so stale *bluefin*.js prefs survived. The
# body is idempotent (mkdir -p / rm -f / cp -rf), so re-running it is safe.
version-script flatpaks privileged 2 || exit 0

set -x

# Set up Firefox default configuration
ARCH=$(arch)
if [ "$ARCH" != "aarch64" ] ; then
	mkdir -p "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref"
	rm -f "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref/"*bluefin*.js
	/usr/bin/cp -rf /usr/share/ublue-os/firefox-config/* "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/${ARCH}/stable/defaults/pref/"
fi
