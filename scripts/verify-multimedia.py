#!/usr/bin/env python3
"""Assert Utah's factory multimedia overrides: identity and codec capability.

Bluefin replaces Fedora's mesa/libva builds with negativo17's by enabling
fedora-multimedia. Utah does not enable that repository, so it takes the same
names from the utah-packages factory instead. install-packages.py adds
[multimedia_overrides] to the install transaction and versionlocks every
override it installs; this verifier asserts the builds the image actually
carries are the factory's, and probes the VA-API codec path.

Source identity is read from the RPM release tag. The factory's Hummingbird
builds carry a `hum` disttag (release `*.hum1.bfin`); negativo17's and Fedora's
builds of the same names do not. Exact NEVRAs move with the factory on every
rebuild, so the assertion checks the factory marker rather than a pinned
version, which would fail the moment the factory rebuilds.
"""

from __future__ import annotations

import argparse
import glob
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# The Hummingbird/factory release marker. A multimedia override whose installed
# release does not carry this came from somewhere other than the factory.
FACTORY_RELEASE_MARKER = "hum"


def section(path: Path, name: str) -> list[str]:
    data = tomllib.loads(path.read_text())
    return list(data.get(name, {}).get("packages", []))


def installed(pkg: str) -> bool:
    return subprocess.run(
        ["rpm", "-q", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def released(pkg: str) -> str:
    out = subprocess.run(
        ["rpm", "-q", "--qf", "%{RELEASE}", pkg],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true",
        help="validate the manifest only; assert nothing about installation",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("overlay", type=Path, nargs="?", default=None)
    args = parser.parse_args()
    overlay = args.overlay or args.manifest.with_name("utah.toml")

    overrides = section(args.manifest, "multimedia_overrides")
    unavailable = set(section(overlay, "unavailable"))

    if args.check:
        assert overrides, "multimedia_overrides section is empty"
        dupes = sorted(p for p in overrides if overrides.count(p) > 1)
        assert not dupes, f"multimedia_overrides contains duplicate names: {dupes}"
        available = sorted(p for p in overrides if p not in unavailable)
        pending = sorted(p for p in overrides if p in unavailable)
        print(
            f"multimedia_overrides: {len(available)} installable from the factory,"
            f" {len(pending)} pending factory publication"
        )
        return 0

    failures: list[str] = []
    for pkg in overrides:
        if pkg in unavailable:
            continue
        if not installed(pkg):
            failures.append(f"{pkg}: not installed")
            continue
        release = released(pkg)
        if FACTORY_RELEASE_MARKER not in release:
            failures.append(
                f"{pkg}: release {release!r} is not a factory (Hummingbird 'hum') build"
            )

    # Representative codec capability: VA-API. libva is always one of the
    # installed overrides and is the front-end the media driver plugs into, so
    # its shared library is a capability marker assertable today. The hardware
    # media driver and vaapi-utils are not published by the factory yet, so
    # vainfo is absent and its probe degrades to a note rather than a false
    # failure; the same probe asserts full capability when they ship.
    if not (glob.glob("/usr/lib64/libva.so*") or glob.glob("/usr/lib/libva.so*")):
        failures.append("libva shared library not present")
    vainfo = shutil.which("vainfo")
    if vainfo:
        out = subprocess.run(["vainfo"], capture_output=True, text=True, check=False)
        probe = (out.stdout + out.stderr).lower()
        if out.returncode != 0 or "no driver" in probe or "error" in probe:
            failures.append("vainfo could not initialise a VA-API driver")
    else:
        print(
            "NOTE: vainfo not yet published by the factory; VA-API capability "
            "asserted here once the media driver and vaapi-utils ship"
        )

    if failures:
        print("multimedia contract failed:", *failures, sep="\n  ", file=sys.stderr)
        return 1
    print("multimedia contract passed: all installed overrides are factory builds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
