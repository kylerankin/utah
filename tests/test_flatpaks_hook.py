"""`99-flatpaks.sh` must clear stale bluefin prefs before seeding Firefox config.

The hook copies ublue-os Firefox defaults into the system flatpak's
`defaults/pref` directory. Before copying it removes leftover `*bluefin*.js`
files from a previous image so a dropped config cannot survive. The glob was
written inside double quotes — `rm -f ".../pref/*bluefin*.js"` — so the shell
matched a *literal* filename that never existed and left stale prefs behind.

Moving the glob outside the quotes changes behaviour (#489): `rm` now actually
deletes the stale `*bluefin*.js` prefs the quoted form never matched. These
tests lock the glob outside the quotes so the bug cannot silently return, and
pin the hook version at 2 so already-provisioned machines re-run the repaired
`rm -f` instead of keeping the prefs version 1 failed to clear.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SHELLCHECK = shutil.which("shellcheck")
HOOK = (
    ROOT
    / "system_files/shared/usr/share/ublue-os/privileged-setup.hooks.d"
    / "99-flatpaks.sh"
)


class FlatpaksHookGlobTests(unittest.TestCase):
    def setUp(self):
        self.body = HOOK.read_text()

    def test_hook_exists(self):
        self.assertTrue(HOOK.is_file())

    def test_glob_is_outside_the_quotes(self):
        """The rm target is a quoted directory followed by an unquoted glob.
        The glob must live outside the quotes, otherwise `rm` matches a literal
        filename and never clears stale prefs (#489)."""
        self.assertIn(
            '/pref/"*bluefin*.js',
            self.body,
            "the *bluefin*.js glob must be outside the double quotes so it "
            "expands instead of matching a literal filename",
        )

    def test_glob_is_not_trapped_in_quotes(self):
        self.assertNotIn(
            'pref/*bluefin*.js"',
            self.body,
            "a glob trapped inside double quotes never expands",
        )

    def test_version_was_bumped_past_the_no_op_rm(self):
        # #489: hosts that ran the version-1 hook recorded success while the
        # quoted glob made `rm -f` match a literal filename, so stale prefs
        # survived. Without a bump the repaired rm never re-runs there.
        self.assertNotIn("flatpaks privileged 1", self.body)
        self.assertIn("version-script flatpaks privileged 2 || exit 0", self.body)

    def test_arch_variable_still_quoted(self):
        # The $ARCH expansion is brace-quoted on every use (mkdir/rm/cp), so a
        # path with a space could not word-split. The fix only moved the
        # trailing glob out of the quotes; ARCH quoting is untouched.
        self.assertEqual(self.body.count("${ARCH}"), 3)

    @unittest.skipUnless(_SHELLCHECK, "shellcheck is not installed")
    def test_shellcheck_passes(self):
        result = subprocess.run(
            [_SHELLCHECK, "--severity=warning", str(HOOK)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"shellcheck failed:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
