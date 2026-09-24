#!/usr/bin/env bash
# Tacklebox live_customize for the Utah live ISO.
#
# Runs inside a container of the Utah image (root, CAP_SYS_ADMIN, network)
# before the live root is squashed. Two jobs:
#
#   1. Bake Utah's default Flatpaks and the bootc-installer bundle into the
#      live squashfs, exactly as iso/live/src/install-flatpaks.sh does for the
#      build-iso.sh path -- the same Brewfile-derived default-flatpak list and
#      the same pinned installer version/SHA. The base image ships no installer
#      (it is added only by the live layer), so this is what gives the
#      tacklebox ISO installer and Flatpak parity with the existing pipeline.
#
#   2. Add the UTAH_LIVE_READY marker service so the CI live-boot gate can
#      observe it on the serial console (#229).
#
# flatpak's bwrap sandbox configures a loopback interface, which CAP_SYS_ADMIN
# alone denies (tuna-os/tacklebox#324). Run tacklebox build with
# TBOX_CUSTOMIZE_CAPS=net_admin so the flatpak bake inside install-flatpaks.sh
# can run in-container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Regenerate the default-flatpak list the same way iso/live/Containerfile does
# -- awk the flatpak app ids out of the Brewfile the common image ships in the
# base image -- then run the same installer script. One source of truth for the
# Flatpak set; the tacklebox path cannot drift from the build-iso.sh path.
awk -F '"' '/^flatpak / {print $2}' \
    /usr/share/ublue-os/homebrew/system-flatpaks.Brewfile > /tmp/flatpaks-list
bash "${ROOT}/iso/live/src/install-flatpaks.sh"

# Live-boot marker for the CI serial gate. Mirrors iso/live/src/configure-live.sh
# but omits its DEBUG branch -- sshd is local-diagnostic only and never ships in
# a published ISO. The marker prints UTAH_LIVE_READY to /dev/ttyS0, which the
# console=ttyS0 karg in utah.json routes to the CI serial log.
cat >/usr/lib/systemd/system/utah-live-ready.service <<'EOF'
[Unit]
Description=Utah live environment ready marker
After=display-manager.service
Requires=display-manager.service

[Service]
Type=oneshot
ExecStart=/bin/echo UTAH_LIVE_READY
StandardOutput=tty
TTYPath=/dev/ttyS0

[Install]
WantedBy=multi-user.target
EOF
mkdir -p /etc/systemd/system-preset
cat >/etc/systemd/system-preset/90-utah-live.preset <<'EOF'
enable utah-live-ready.service
enable var-tmp.mount
EOF
systemctl enable utah-live-ready.service
