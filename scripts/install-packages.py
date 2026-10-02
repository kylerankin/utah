#!/usr/bin/env python3
"""Install Utah's package contract: Bluefin's manifest plus the Utah overlay.

Mirrors projectbluefin/bluefin's build_files/base/03-packages.sh and
build_files/shared/package-lib.sh, adapted to Utah's repositories: the pinned
utah-packages factory repository plus Hummingbird's own.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

def install_repos(repo_dir: Path | None = None) -> tuple[str, ...]:
    """The repositories the package transaction installs from.

    Derived from packages/*.repo (or /etc/yum.repos.d in-image) — the same
    files copied into /etc/yum.repos.d — so a renamed repository changes what
    is enabled instead of leaving a stale hardcoded copy.
    A repository belongs to the install transaction when its section carries a
    `# utah-install: true` annotation. Repositories are ordered by priority
    (ascending, lowest number first) so rebuilds in utah-packages (priority=1) win
    over base Hummingbird packages (priority=10).
    """
    search_dirs: list[Path] = []
    if repo_dir:
        search_dirs.append(repo_dir)
    else:
        search_dirs.append(Path("/etc/yum.repos.d"))
        search_dirs.append(Path(__file__).resolve().parent.parent / "packages")

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        repos: list[tuple[int, str]] = []
        for repo_file in sorted(directory.glob("*.repo")):
            section_name: str | None = None
            marked = False
            pending_marker = False
            priority = 99
            for line in repo_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    if section_name and marked:
                        repos.append((priority, section_name))
                    section_name = line[1:-1]
                    marked = pending_marker
                    pending_marker = False
                    priority = 99
                elif line.startswith("#"):
                    comment = line.lstrip("#").strip()
                    if comment == "utah-install: true":
                        pending_marker = True
                elif "=" in line:
                    if pending_marker:
                        marked = True
                        pending_marker = False
                    key, val = line.split("=", 1)
                    if key.strip() == "priority":
                        try:
                            priority = int(val.strip())
                        except ValueError:
                            pass
            if pending_marker:
                marked = True
            if section_name and marked:
                repos.append((priority, section_name))

        if repos:
            repos.sort()
            return tuple(name for _, name in repos)

    if repo_dir:
        raise ValueError(f"no repositories marked '# utah-install: true' under {repo_dir}")
    raise ValueError("no repositories marked '# utah-install: true' found in search paths")


# Utah installs only from its Hummingbird base plus the utah-packages
# factory, which publishes every GNOME 51 and Bluefin-parity binary this
# image needs rebuilt against Hummingbird. Fedora repositories are never
# enabled at runtime: they are bootstrap material for the package factory's
# buildroot, not a source of installed packages.
# The factory is first so its Hummingbird-targeted rebuilds win over an
# equally-versioned Hummingbird package. The repository is copied from the
# digest-pinned OCI package image by Containerfile.
def __getattr__(name: str):
    if name == "REPOS":
        return install_repos()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def section(path: Path, name: str) -> list[str]:
    data = tomllib.loads(path.read_text())
    return list(data.get(name, {}).get("packages", []))


def fedora_major() -> str:
    """Read %fedora the way Bluefin's build scripts do."""
    out = subprocess.run(
        ["rpm", "-E", "%fedora"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def contract(base: Path, overlay: Path, major: str | None) -> list[str]:
    """The exact set of packages the built image must contain.

    Bluefin installs [fedora] plus the [fedora_v<major>] section for the
    Fedora release it targets, and simply skips that section when it does not
    exist.  The lookup is dynamic: whatever release the base image reports,
    Utah installs the matching section when upstream defines one and skips it
    otherwise, so a new upstream section is picked up for free.
    """
    packages = section(base, "fedora")
    if major:
        packages += section(base, f"fedora_v{major}")
    packages += section(overlay, "gnome")
    # Parity with what Bluefin inherits from Fedora's base image and Hummingbird
    # has in its repository but not in its bootable base.
    packages += section(overlay, "parity")
    # Device firmware the bootable base leaves out altogether. Its own section
    # rather than [parity], because it is not parity with anything: Hummingbird
    # has no linux-firmware to inherit, the factory builds it, and nothing in
    # the image Requires it. A section named here is a section that installs;
    # one that is not is read by nobody and ships nothing.
    packages += section(overlay, "hardware")
    # Service packages are part of the desktop contract as well: 40-services.sh
    # cannot enable what the server base never installed.
    packages += section(overlay, "services")
    unavailable = set(section(overlay, "unavailable"))
    # Deduplicate while preserving order so build logs stay diffable.
    seen: dict[str, None] = {}
    for pkg in packages:
        if pkg not in unavailable:
            seen.setdefault(pkg, None)
    return list(seen)


def dnf_path() -> str:
    dnf = shutil.which("dnf5") or shutil.which("dnf")
    if not dnf:
        raise RuntimeError("Hummingbird base does not provide dnf or dnf5")
    return dnf


def run(*args: str) -> int:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, check=False).returncode


def installed(packages: list[str]) -> list[str]:
    if not packages:
        return []
    out = subprocess.run(
        ["rpm", "-qa", "--queryformat=%{NAME}\n", *packages],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(set(out.stdout.split()))


# A declined --assumeno run exits nonzero for a *valid* transaction, so these
# markers are what separate "the package exists and installs" from "the
# package is absent or its dependencies cannot be met": the absence is what
# an [unavailable] entry must keep showing.
# "Error:"/"Failed to" are environment failures (unreachable repo, broken
# mount, failed download): they mean the check could not run, not that the
# package is unavailable. "No match"/"nothing provides" are the genuine signal
# an [unavailable] entry must keep showing.
_UNAVAILABLE_ERROR_RE = re.compile(
    r"No match for argument|nothing provides|conflicting requests|cannot install both"
)
_ENVIRONMENT_ERROR_RE = re.compile(r"Error:|Failed to")
_UNAVAILABLE_SUMMARY_RE = re.compile(
    r"(?m)^Transaction Summary:?\s*$|^Nothing to do\.?\s*$"
)


class _RepoError(Exception):
    """A package check could not run: the repository environment is broken
    (unreachable, bad repodata, failed download). Raised so the gate fails
    closed instead of reading the failure as an unavailable package."""


def is_installable(dnf: str, name: str, repos: tuple[str, ...]) -> bool:
    """True when the pinned repository can satisfy a single package: a valid
    Transaction Summary with no match or dependency error. The --assumeno run
    declines the install, so dnf exits nonzero for a valid transaction; a
    missing package or broken dependency is rejected explicitly rather than
    accepted as that decline.

    Raises _RepoError when the check cannot run at all (unreachable repo,
    broken mount, failed download): that is an environment failure that must
    fail the gate, never masquerade as a deliberate [unavailable] gap."""
    out = subprocess.run(
        [dnf, "--assumeno", "--disablerepo=*",
         *(f"--enablerepo={r}" for r in repos),
         "install", name],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "LC_ALL": "C"}, check=False,
    )
    if _UNAVAILABLE_ERROR_RE.search(out.stdout):
        # A missing package or dependency conflict is genuinely unavailable:
        # check this before the environment regex, because dnf5 reports a
        # missing package as "Failed to resolve the transaction:" (which also
        # matches _ENVIRONMENT_ERROR_RE). Treating the unavailable marker first
        # keeps a real [unavailable] entry from being misread as an environment
        # error that fails the gate.
        return False
    if _ENVIRONMENT_ERROR_RE.search(out.stdout) or out.returncode == 125:
        raise _RepoError(out.stdout.strip() or f"dnf exited {out.returncode}")
    return (
        out.returncode in (0, 1)
        and re.search(_UNAVAILABLE_SUMMARY_RE, out.stdout)
    )


def assert_unavailable(dnf: str, repos: tuple[str, ...], overlay: Path) -> int:
    """Fail (exit 1) when any [unavailable] entry is now installable in the
    pinned repository. Each [unavailable] entry is a deliberate gap in Utah's
    repositories -- a package the image cannot install and never should. That
    gap is only real while the package stays unavailable; an upstream release
    can make it installable without touching the manifest, silently adding a
    package the contract never intended. Point at the manifest so the
    contributor knows where to triage."""
    unavailable = section(overlay, "unavailable")
    if not unavailable:
        print("No [unavailable] entries; nothing to assert")
        return 0
    installable = []
    for name in unavailable:
        try:
            wanted = is_installable(dnf, name, repos)
        except _RepoError as error:
            # The environment could not be queried; fail the gate rather than
            # read the failure as "this entry stays unavailable".
            print(f"ERROR: repository check failed for {name!r} ({error}); "
                  f"cannot confirm this [unavailable] entry is still unavailable",
                  file=sys.stderr)
            return 1
        if wanted:
            installable.append(name)
    if installable:
        print(f"ERROR: {len(installable)} [unavailable] entry(ies) are now installable:",
              file=sys.stderr)
        for name in installable:
            print(f"  - {name} (see packages/utah.toml)", file=sys.stderr)
        return 1
    print(f"All {len(unavailable)} [unavailable] entries remain unavailable")
    return 0


EVR_QUERYFORMAT = "%{NAME} %{ARCH} %{EPOCH}:%{VERSION}-%{RELEASE}\n"


def evr_map(lines: list[str]) -> dict[tuple[str, str], str]:
    """(name, arch) -> EVR for rpm/repoquery --queryformat output lines.

    Unparseable lines (rpm's "package X is not installed") are skipped, so a
    package that is absent is simply not in the map. Epochs normalize to 0:
    rpm prints "(none)" and repoquery agrees, but the comparison must not
    depend on that.
    """
    out: dict[tuple[str, str], str] = {}
    for line in lines:
        parts = line.split()
        if len(parts) == 3:
            out[(parts[0], parts[1])] = parts[2].replace("(none):", "0:")
    return out


def installed_evr(packages: list[str]) -> dict[tuple[str, str], str]:
    if not packages:
        return {}
    out = subprocess.run(
        ["rpm", "-q", "--queryformat=" + EVR_QUERYFORMAT, *packages],
        capture_output=True,
        text=True,
        check=False,
    )
    return evr_map(out.stdout.splitlines())


def repo_evr(dnf: str, repoid: str, packages: list[str]) -> dict[tuple[str, str], str] | None:
    """Latest EVR per (name, arch) the repository offers, or None when the
    repository is unavailable (off-image runs, where the mount is absent)."""
    if not packages:
        return {}
    out = subprocess.run(
        [dnf, "repoquery", "--disablerepo=*", f"--enablerepo={repoid}",
         "--latest-limit=1", "--queryformat=" + EVR_QUERYFORMAT, *packages],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return None
    return evr_map(out.stdout.splitlines())


def find_skew(installed_map: dict[tuple[str, str], str],
              available: dict[tuple[str, str], str]) -> list[str]:
    """Installed packages whose EVR differs from the repository's latest.

    Only packages present on both sides are compared: presence is the RPM
    contract verifier's job, and a factory-section package the factory does
    not (or no longer) offer is not skew against it.
    """
    skew = []
    for key, ievr in sorted(installed_map.items()):
        aevr = available.get(key)
        if aevr is not None and aevr != ievr:
            skew.append(f"{key[0]}.{key[1]} installed {ievr} but repository offers {aevr}")
    return skew


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resolve", action="store_true",
                        help="resolve the full transaction without installing packages")
    parser.add_argument("--assert-unavailable", action="store_true",
                        help="fail if any [unavailable] entry is now installable in the pinned repository")
    parser.add_argument("--repos-dir", type=Path, default=None,
                        help="directory containing .repo files (defaults to /etc/yum.repos.d or packages/)")
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "overlay", type=Path, nargs="?", default=None,
        help="defaults to utah.toml alongside the Bluefin manifest",
    )
    args = parser.parse_args()
    overlay = args.overlay or args.manifest.with_name("utah.toml")
    repos = install_repos(args.repos_dir)

    if args.check:
        # No rpmdb to consult off-image, so validate the manifests only.
        packages = contract(args.manifest, overlay, major=None)
        if not packages:
            raise ValueError("Bluefin package manifest is empty")
        unavailable = section(overlay, "unavailable")
        overlap = sorted(set(unavailable) & set(packages))
        if overlap:
            raise ValueError(f"[unavailable] packages still in install set: {overlap}")
        if "utah-packages" not in repos:
            raise ValueError("utah-packages repository not found in install repositories")
        if "public-hummingbird-x86_64-rpms" not in repos:
            raise ValueError("public-hummingbird-x86_64-rpms repository not found in install repositories")
        print(f"validated {len(packages)} Bluefin parity packages")
        print(f"documented as unavailable: {len(unavailable)}")
        print(f"install repositories: {', '.join(repos)}")
        return 0

    dnf = dnf_path()
    major = fedora_major()

    if args.assert_unavailable:
        return assert_unavailable(dnf, repos, overlay)

    packages = contract(args.manifest, overlay, major)
    build_deps = section(overlay, "build")
    excluded = section(args.manifest, "excluded")

    if args.resolve:
        result = subprocess.run(
            [dnf, "--assumeno", "--disablerepo=*",
             *(f"--enablerepo={r}" for r in repos),
             "-x", "PackageKit*", "install", *packages, *build_deps],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "LC_ALL": "C"}, check=False,
        )
        print(result.stdout, end="", flush=True)
        # DNF exits nonzero when --assumeno declines a valid transaction.
        # A missing package or dependency must never be accepted as that case.
        errors = r"No match for argument|nothing provides|conflicting requests|cannot install both|Error:|Failed to"
        summary = r"(?m)^Transaction Summary:?\s*$|^Nothing to do\.?\s*$"
        if (result.returncode not in (0, 1)
                or re.search(errors, result.stdout, re.IGNORECASE)
                or not re.search(summary, result.stdout)):
            return 1
        print(f"Resolved {len(set(packages + build_deps))} runtime and build packages on the pinned base")
        return 0

    # Record exactly what this run resolved, so the contract check asserts the
    # set that was actually asked for rather than recomputing it and drifting.
    # It drifted once: install added [fedora_v<major>] and the verifier never
    # did, so a contract package was installed and never checked.
    resolved = Path("/usr/share/utah/contract.txt")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("".join(f"{pkg}\n" for pkg in packages))
    except OSError as error:
        print(f"WARNING: could not record the resolved contract: {error}")

    print(f"Fedora release is {major}", flush=True)
    for pkg in section(overlay, "unavailable"):
        # Loud, not silent: a parity gap the operator should see in the log.
        print(f"NOTE: {pkg} has no source in Utah's repositories and is skipped (see packages/utah.toml)")

    # Bluefin excludes PackageKit from its bulk install; an image-based system
    # must not carry a second package manager that can write to /usr.
    rc = run(
        dnf, "-y", "--disablerepo=*",
        *(f"--enablerepo={r}" for r in repos),
        "-x", "PackageKit*", "install", *packages, *build_deps,
    )
    if rc:
        return rc

    # Everything in the contract is installed on purpose.  Without this, dnf
    # treats packages that merely arrived as dependencies as autoremovable,
    # and the [excluded] removal below drags them back out -- which is how
    # xdg-desktop-portal-gnome disappeared from an image that installed it.
    rc = run(dnf, "-y", "mark", "user", *packages, *build_deps)
    if rc:
        return rc

    # Mirror remove_excluded_packages: only remove what is actually installed,
    # and never let the removal cascade into the contract.
    present = installed(excluded)
    if present:
        print(f"Removing {len(present)} excluded packages: {' '.join(present)}")
        # dnf5 requires --no-autoremove after the subcommand, not before it:
        # "The argument is available for commands: remove. (It has to be placed
        # after the command.)" dnf4 accepts either position, so this ordering
        # works on both.
        rc = run(dnf, "-y", "remove", "--no-autoremove", *present)
        if rc:
            return rc
    else:
        print("No excluded packages found to remove.")

    # Fail loud on a stale transaction. The contract verifier only asserts
    # presence, so a layer-cached install of the previous factory's packages
    # passed every gate and shipped (#371). The factory-exclusive sections
    # must match what the mounted repository offers right now, not what an
    # older transaction left behind.
    factory = sorted(set(section(overlay, "gnome")) | set(section(overlay, "hardware")))
    if factory:
        available = repo_evr(dnf, "utah-packages", factory)
        if available is None:
            print("NOTE: utah-packages repository is unavailable; skipping version-skew check")
        else:
            have = installed_evr(factory)
            skew = find_skew(have, available)
            if skew:
                print(f"ERROR: {len(skew)} installed packages differ from the pinned factory repository:",
                      file=sys.stderr)
                for line in skew:
                    print(f"  - {line}", file=sys.stderr)
                return 1
            compared = sum(1 for key in have if key in available)
            print(f"Factory versions verified against utah-packages for {compared} packages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
