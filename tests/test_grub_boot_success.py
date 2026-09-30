"""grub-boot-success must never fail on a Utah system.

`grub-boot-success.service` ships from the Hummingbird base image and is
enabled by preset; `grub-boot-success.timer` (a user unit, enabled by
`scripts/configure-services.sh`) fires it periodically. Its script calls
`grub2-editenv`, which writes `/boot/grub2/grubenv` -- but ostree remounts
`/boot` read-only at runtime, so on Utah the unit fails on every fire, and
nothing consumes the flag. On a systemd-boot system the same unit is the only
failed service (issue #364).

The fix is a drop-in that skips the unit where `/boot` is not writable, i.e.
where the system is ostree-booted. It mirrors
`bootc-unified-storage.service`, which gates itself with
`ConditionPathExists=/run/ostree-booted`. On an ostree system the condition
fails and systemd skips the unit instead of failing it -- that is exactly what
the systemd-boot case in #364 also sees.

Without this test a future editor could ship an image whose only failed unit
is the grub boot-success marker on every boot.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSTREE_BOOTED = "/run/ostree-booted"
DROPIN = ROOT / "system_files/shared/etc/systemd/system/grub-boot-success.service.d/utah-boot-success.conf"


def condition_path_exists(text: str) -> list[str]:
    """Value of every active `ConditionPathExists=`, skipping comments.

    systemd treats a `#` at the start of a line as a comment, so a value only
    counts when it is the first non-blank character on the line. The `!`
    negation prefix is stripped so the test can assert the unit is skipped on
    ostree rather than only-run-on-ostree.
    """
    values = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("!ConditionPathExists="):
            values.append(stripped[len("!ConditionPathExists="):].strip())
        elif stripped.startswith("ConditionPathExists="):
            values.append(stripped[len("ConditionPathExists="):].strip())
    return values


class GrubBootSuccessDropInTests(unittest.TestCase):
    def test_drop_in_is_shipped(self):
        self.assertTrue(DROPIN.is_file(), "drop-in not shipped under system_files/shared")

    def test_drop_in_skips_on_ostree(self):
        """The unit must be skipped where /run/ostree-booted is present, i.e. on
        every Utah system, so systemd skips it instead of failing it."""
        self.assertIn(
            OSTREE_BOOTED,
            condition_path_exists(DROPIN.read_text()),
            "the unit must be skipped where /run/ostree-booted is present, i.e. "
            "on every Utah system, so systemd skips it instead of failing it",
        )

    def test_condition_is_active_and_negated(self):
        """The condition must be an active negation, so the unit runs off-ostree
        rather than only-on-ostree."""
        self.assertTrue(
            any(l.lstrip().startswith("!ConditionPathExists=") for l in DROPIN.read_text().splitlines()),
            "!ConditionPathExists=/run/ostree-booted must be active, not commented out",
        )


if __name__ == "__main__":
    unittest.main()
