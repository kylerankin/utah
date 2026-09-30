"""The four hooks migrated here must stamp completion only after their bodies run.

`projectbluefin/common#1196` splits `version-script` into a read-only check and
a commit: the legacy helper records the version *before* the body runs, so a
hook that fails on first boot is skipped forever after.  Each hook below gates
on `version-script-check`, runs under `set -e`, and calls
`version-script-commit` only once its body has completed.

`20-home-labels.sh` migrated separately and keeps its own suite in
`test_setup_hook_version_contract.py`.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVILEGED = ROOT / "system_files/shared/usr/share/ublue-os/privileged-setup.hooks.d"
USER = ROOT / "system_files/shared/usr/share/ublue-os/user-setup.hooks.d"

# hook path -> (version-script arguments, `set -e` line, a body line that must
# sit between the check and the final commit)
HOOKS = {
    PRIVILEGED / "10-tailscale.sh": (
        "tailscale privileged 1",
        "set -xeuo pipefail",
        'tailscale set --operator="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"',
    ),
    PRIVILEGED / "11-framework-ucsi-workaround.sh": (
        "framework-ucsi-workaround privileged 1",
        "set -euo pipefail",
        'rpm-ostree kargs --append-if-missing="${WORKAROUND_KARG}"',
    ),
    PRIVILEGED / "99-flatpaks.sh": (
        "flatpaks privileged 1",
        "set -xe",
        "/usr/bin/cp -rf /usr/share/ublue-os/firefox-config/* "
        '"/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/'
        '${ARCH}/stable/defaults/pref/"',
    ),
    USER / "20-framework.sh": (
        "20-framework user 1",
        "set -euo pipefail",
        'install_if_missing "fw-ectool"',
    ),
}


class MigratedSetupHookContractTests(unittest.TestCase):
    @staticmethod
    def _lines(hook):
        return [
            line.strip()
            for line in hook.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_hooks_exist(self):
        for hook in HOOKS:
            with self.subTest(hook=hook.name):
                self.assertTrue(hook.is_file())

    def test_does_not_use_the_legacy_burn_before_body_gate(self):
        for hook, (args, _, _) in HOOKS.items():
            with self.subTest(hook=hook.name):
                self.assertNotIn(
                    f"version-script {args}",
                    self._lines(hook),
                    "the legacy helper stamps completion before the body runs",
                )

    def test_checks_before_it_acts(self):
        for hook, (args, _, body) in HOOKS.items():
            with self.subTest(hook=hook.name):
                lines = self._lines(hook)
                check = f"version-script-check {args} || exit 0"
                self.assertIn(check, lines)
                self.assertIn(body, lines)
                self.assertLess(lines.index(check), lines.index(body))

    def test_commits_after_the_body(self):
        for hook, (args, _, body) in HOOKS.items():
            with self.subTest(hook=hook.name):
                lines = self._lines(hook)
                commit = f"version-script-commit {args}"
                self.assertIn(commit, lines)
                # Hooks may also commit on deliberate skip paths; the success
                # stamp is the last one, and it must follow the body.
                last_commit = len(lines) - 1 - lines[::-1].index(commit)
                self.assertLess(lines.index(body), last_commit)

    def test_body_failure_aborts_before_the_commit(self):
        # `set -e` is what makes a failing body stop the hook instead of
        # running on to record a completion that never happened.
        for hook, (_, set_e, body) in HOOKS.items():
            with self.subTest(hook=hook.name):
                lines = self._lines(hook)
                self.assertIn(set_e, lines)
                self.assertLess(lines.index(set_e), lines.index(body))

    def test_compat_shim_covers_pre_1196_libsetup(self):
        # The pinned COMMON_IMAGE_SHA has no version-script-check/-commit yet,
        # so the shim keeps the hooks working under both contracts in either
        # merge order.
        for hook in HOOKS:
            with self.subTest(hook=hook.name):
                body = hook.read_text()
                self.assertIn(
                    "if ! declare -F version-script-check >/dev/null; then", body
                )
                self.assertIn("version-script-commit() { :; }", body)


if __name__ == "__main__":
    unittest.main()
