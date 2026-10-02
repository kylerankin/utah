"""First-boot service and hook validation (issue #19).

The privileged/user setup hooks run at first boot. Before this work they had no
coverage, so a hook that invoked a missing binary (tailscale) or a missing
optional directory (firefox-config) could fail first-boot setup without anything
in CI noticing.

These tests:

* execute the real hooks against a stubbed ``libsetup.sh`` and stubbed userland
  (no root, no image build) and assert the runtime behaviours the issue calls
  out: a missing binary defers cleanly instead of failing, the operator grant is
  deferred until a real calling UID exists, version stamping is idempotent, and
  the optional firefox-config copy is skipped rather than failing when absent.
* assert the first-boot enablement policy lives in the preset and the desktop
  contract, and that the shipped contract validates.
"""

import importlib.util
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "system_files" / "shared" / "usr" / "share" / "ublue-os"
TAILSCALE_HOOK = HOOKS_DIR / "privileged-setup.hooks.d" / "10-tailscale.sh"
FLATPAKS_HOOK = HOOKS_DIR / "privileged-setup.hooks.d" / "99-flatpaks.sh"
PRESET = (
    ROOT / "system_files" / "shared" / "usr" / "lib" / "systemd"
    / "system-preset" / "85-utah-desktop.preset"
)
CONTRACT = ROOT / "contracts" / "bluefin-desktop.toml"

BASH = shutil.which("bash")

# Faithful stub of libsetup.sh's version-script: mirrors its contract exactly -
# exit 1 (skip) when the recorded version already equals the target, otherwise
# stamp the target version and exit 0. The hooks call it as
# `version-script <name> <type> <n> || exit 0`, so a non-zero return defers the
# hook body. Only builtins are used so the stub needs no external binaries.
# The stamp is a tiny "name|version" line (not JSON) purely to keep the case
# pattern free of quote-escaping.
STUB_LIBSETUP = '''\
SETUP_CHECKER_FILE="${SETUP_CHECKER_FILE:-$HOME/.local/share/ublue/setup_versioning.json}"
version-script() {
  _name=$1
  _ver=$3
  mkdir -p "${SETUP_CHECKER_FILE%/*}"
  if [ -f "$SETUP_CHECKER_FILE" ]; then
    read _content < "$SETUP_CHECKER_FILE" 2>/dev/null || _content=""
    case "$_content" in
      *"${_name}|${_ver}"*) echo "skip ${_name} ${_ver}"; return 1 ;;
    esac
  fi
  printf '%s|%s\n' "${_name}" "${_ver}" > "$SETUP_CHECKER_FILE"
  return 0
}
'''

# Stub userland used by the tailscale hook. getent returns a passwd line; cut
# extracts the username field, which the hook passes as --operator.
STUB_GETENT = "#!/usr/bin/bash\necho \"alice:x:1000:1000::/home/alice::\"\nexit 0\n"
STUB_CUT = "#!/usr/bin/bash\nwhile IFS= read -r line; do echo \"${line%%:*}\"; done\nexit 0\n"
STUB_TAILSCALE = "#!/usr/bin/bash\necho \"$*\" >> \"$TAILSCALE_LOG\"\nexit 0\n"

# The flatpaks hook writes into /var/lib/flatpak (needs root) and shells out to
# mkdir/rm/cp/arch; stub them so the guard logic is exercised without root.
STUB_TOOL = "#!/usr/bin/bash\nexit 0\n"
STUB_ARCH = "#!/usr/bin/bash\necho x86_64\nexit 0\n"


class FirstBootEnv:
    """A temp sandbox letting a hook resolve stubbed libsetup.sh + userland.

    ``PATH`` is the sandbox only, and bash is invoked by absolute path, so the
    stubs fully shadow the real userland and a deliberately-absent binary is
    genuinely absent rather than found on the host.
    """

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="first-boot-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.versioning = Path(self.tmp) / "setup_versioning.json"
        self.tailscale_log = Path(self.tmp) / "tailscale.log"
        self.env = {
            "PATH": str(self.bin),
            "SETUP_CHECKER_FILE": str(self.versioning),
            "TAILSCALE_LOG": str(self.tailscale_log),
        }

    def _install(self, name, body):
        target = self.bin / name
        target.write_text(body)
        target.chmod(0o755)

    def install_libsetup(self):
        (Path(self.tmp) / "libsetup.sh").write_text(STUB_LIBSETUP)

    def install(self, names):
        mapping = {
            "getent": STUB_GETENT,
            "cut": STUB_CUT,
            "tailscale": STUB_TAILSCALE,
            "mkdir": STUB_TOOL,
            "rm": STUB_TOOL,
            "cp": STUB_TOOL,
            "arch": STUB_ARCH,
        }
        for name in names:
            self._install(name, mapping[name])

    def run_hook(self, hook=TAILSCALE_HOOK, firefox_root=None):
        """Run a byte-for-byte copy of the real hook, with one path rewritten.

        The hook sources libsetup.sh from a hardcoded absolute path, so the copy
        rewrites that one line to the sandbox stub. When ``firefox_root`` is set
        the hardcoded firefox-config path is rewritten too, letting the "present"
        branch be exercised without root. When unset, it points to a non-existent
        sandbox path so the host's `/usr/share/ublue-os/firefox-config` is not read.
        """
        source = hook.read_text()
        source = source.replace(
            "source /usr/lib/ublue/setup-services/libsetup.sh",
            f'source {self.tmp}/libsetup.sh',
        )
        target_firefox = str(firefox_root) if firefox_root is not None else f"{self.tmp}/no-such-firefox-config"
        source = source.replace(
            "/usr/share/ublue-os/firefox-config", target_firefox,
        )
        hook_copy = Path(self.tmp) / "hook.sh"
        hook_copy.write_text(source)
        hook_copy.chmod(0o755)
        return subprocess.run(
            [BASH, str(hook_copy)], env=self.env, capture_output=True, text=True,
        )
    def versioning_stamped(self, name, version):
        if not self.versioning.exists():
            return False
        return f"{name}|{version}" in self.versioning.read_text()


def _load_module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DESKTOP = _load_module("verify-desktop-contract")


class TestTailscaleHook(unittest.TestCase):
    def test_missing_binary_defers_without_failing(self):
        env = FirstBootEnv()
        env.install_libsetup()
        env.install(["getent"])  # no tailscale on PATH
        proc = env.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(env.versioning_stamped("tailscale", "1"))
        self.assertIn("deferring", proc.stdout.lower())
        self.assertFalse(env.tailscale_log.exists())

    def test_present_binary_grants_operator(self):
        env = FirstBootEnv()
        env.install_libsetup()
        env.install(["getent", "cut", "tailscale"])
        env.env["PKEXEC_UID"] = "1000"
        proc = env.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(env.versioning_stamped("tailscale", "1"))
        self.assertIn("--operator=alice", env.tailscale_log.read_text())

    def test_missing_calling_uid_defers(self):
        env = FirstBootEnv()
        env.install_libsetup()
        env.install(["getent", "cut", "tailscale"])
        env.env.pop("PKEXEC_UID", None)
        proc = env.run_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(env.versioning_stamped("tailscale", "1"))
        self.assertFalse(env.tailscale_log.exists())
        self.assertIn("no usable calling uid", proc.stdout.lower())

    def test_version_stamp_is_idempotent(self):
        env = FirstBootEnv()
        env.install_libsetup()
        env.install(["getent", "cut", "tailscale"])
        env.env["PKEXEC_UID"] = "1000"
        self.assertEqual(env.run_hook().returncode, 0)
        self.assertTrue(env.versioning_stamped("tailscale", "1"))
        env.run_hook()
        # Exactly one operator grant across both boots.
        self.assertEqual(env.tailscale_log.read_text().count("--operator=alice"), 1)

class TestFlatpaksHook(unittest.TestCase):
    def _flatpaks_env(self):
        env = FirstBootEnv()
        env.install_libsetup()
        env.install(["mkdir", "rm", "cp", "arch"])
        return env

    def test_missing_firefox_config_skips_cleanly(self):
        env = self._flatpaks_env()
        proc = env.run_hook(hook=FLATPAKS_HOOK)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("firefox-config not present", proc.stdout)

    def test_present_firefox_config_copies(self):
        env = self._flatpaks_env()
        firefox_root = Path(tempfile.mkdtemp(prefix="firefox-config-"))
        (firefox_root / "bluefin.js").write_text("pref('x', 1);")
        proc = env.run_hook(hook=FLATPAKS_HOOK, firefox_root=firefox_root)
        # The guard must take the copy branch when firefox-config is present.
        # cp writes to /var/lib/flatpak (root-only), so we assert the branch was
        # taken rather than the exit code, which a no-root sandbox cannot satisfy.
        self.assertNotIn("firefox-config not present", proc.stdout)
        # The hook copies from the (rewritten) firefox_root, which lives under
        # TMPDIR — assert on the real path rather than a hardcoded /tmp prefix.
        self.assertIn(str(firefox_root), proc.stderr)


class TestEnablementPolicy(unittest.TestCase):
    def test_contract_validates(self):
        data = tomllib.loads(CONTRACT.read_text())
        self.assertEqual(DESKTOP.validate_contract(data), [])

    def test_contract_asserts_first_boot_services(self):
        data = tomllib.loads(CONTRACT.read_text())
        enabled = data["services"]["enabled"]
        self.assertIn("bluefin-stats-refresh.timer", enabled)
        self.assertIn("input-remapper.service", enabled)

    def test_preset_enables_first_boot_services(self):
        preset = PRESET.read_text()
        self.assertIn("enable bluefin-stats-refresh.timer", preset)
        self.assertIn("enable input-remapper.service", preset)

if __name__ == "__main__":
    import unittest

    unittest.main()
