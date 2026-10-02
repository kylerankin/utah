"""`99-flatpaks.sh` must clear stale bluefin prefs before seeding Firefox config.

The hook copies ublue-os Firefox defaults into the system flatpak's
`defaults/pref` directory. Before copying it removes leftover `*bluefin*.js`
files from a previous image so a dropped config cannot survive. The glob was
written inside double quotes — `rm -f ".../pref/*bluefin*.js"` — so the shell
matched a *literal* filename that never existed and left stale prefs behind.
This is a style-only regression (#489): the common-case behaviour is unchanged,
but the test locks the glob outside the quotes so the bug cannot silently
return.
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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

    def test_arch_variable_still_quoted(self):
        # The $ARCH expansion is brace-quoted on every use (mkdir/rm/cp), so a
        # path with a space could not word-split. The fix only moved the
        # trailing glob out of the quotes; ARCH quoting is untouched.
        self.assertEqual(self.body.count("${ARCH}"), 3)
    def test_shellcheck_passes(self):
        result = subprocess.run(
            ["shellcheck", "--severity=warning", str(HOOK)],
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
