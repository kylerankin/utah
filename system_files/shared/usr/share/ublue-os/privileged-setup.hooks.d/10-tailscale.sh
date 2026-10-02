#!/usr/bin/bash

# shellcheck source=/dev/null
source /usr/lib/ublue/setup-services/libsetup.sh

set -euo pipefail

# Tailscale is optional on Utah. If the binary is absent (a minimal build, or
# the package was never selected) defer setup instead of failing the first-boot
# hook: stamping a version here would mark the hook done before it ever ran, so
# a later tailscale install would never complete the operator grant. Record the
# deferred state in the log so users and CI can see setup is pending, and retry
# on the next boot once the binary exists.
if ! command -v tailscale >/dev/null 2>&1; then
    echo "tailscale is not installed; deferring privileged operator setup until it is present"
    exit 0
fi

# Without a calling UID there is no operator to grant. A missing PKEXEC_UID would
# otherwise resolve the getent lookup to root, granting the operator role to the
# wrong account, so defer until the hook runs from a real pkexec context.
if [ -z "${PKEXEC_UID:-}" ] || ! getent passwd "${PKEXEC_UID}" >/dev/null 2>&1; then
    echo "no usable calling UID; deferring tailscale privileged setup until run under pkexec"
    exit 0
fi

version-script tailscale privileged 1 || exit 0

tailscale set --operator="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
