"""Service enablement policy is declared once and enforced in three places.

First boot only succeeds cleanly when every service the image relies on is
enabled by the same policy. The preset, `scripts/configure-services.sh`, and the
desktop contract are three independent sources that must agree: the preset and
configure-services.sh actually enable the unit at build/first boot, and the
contract is the in-image verifier that asserts the unit is enabled. A change
that enables a unit in one source but not the others is silent drift that
leaves a service off at boot with no test failing.

This test closes #19 criterion 3: input-remapper and Bluefin community stats
refresh are enabled intentionally and consistently across all three sources,
and the stats timer is gated on a writable cache so a missing /var/cache is a
visible, retryable condition rather than a failed unit.
"""

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESET = ROOT / "system_files/shared/usr/lib/systemd/system-preset/85-utah-desktop.preset"
CONFIGURE_SERVICES = ROOT / "scripts/configure-services.sh"
CONTRACT = tomllib.loads((ROOT / "contracts/bluefin-desktop.toml").read_text())

# Services that must be enabled by the desktop policy. input-remapper ships in
# Bluefin's package set but needs its root daemon for udev autoload; the stats
# timer refreshes fastfetch's community data on boot and every day.
ENABLED_SERVICES = ("input-remapper.service", "bluefin-stats-refresh.timer")


class ServiceEnablementPolicyTests(unittest.TestCase):
    def test_preset_enables_required_desktop_services(self):
        preset = PRESET.read_text()
        for unit in ENABLED_SERVICES:
            with self.subTest(unit=unit):
                self.assertIn(f"enable {unit}", preset)

    def test_configure_services_script_enables_required_desktop_services(self):
        script = CONFIGURE_SERVICES.read_text()
        for unit in ENABLED_SERVICES:
            with self.subTest(unit=unit):
                self.assertIn(f"enable_unit {unit}", script)

    def test_desktop_contract_declares_required_services(self):
        enabled = CONTRACT.get("services", {}).get("enabled", [])
        for unit in ENABLED_SERVICES:
            with self.subTest(unit=unit):
                self.assertIn(unit, enabled)

    def test_stats_timer_degrades_on_unwritable_cache(self):
        # The stats refresh runs as a dynamic user that only needs a writable
        # /var/cache; without the guard a transient read-only cache would fail
        # the unit instead of deferring. See #19.
        service = (
            ROOT
            / "system_files/shared/usr/lib/systemd/system/bluefin-stats-refresh.service"
        ).read_text()
        self.assertIn("ConditionPathIsReadWrite=/var/cache", service)


if __name__ == "__main__":
    unittest.main()
