#!/usr/bin/env python3
"""Fail when an [unavailable] entry becomes installable in the pinned repository.

The [unavailable] section of packages/utah.toml is a deliberate gap: a package
the image cannot install and never should. That gap is only real while the
package stays unavailable. A new upstream release can make it installable
without touching the manifest, silently adding a package the contract never
intended. This resolves the pinned repository against the base image's dnf and
asserts every [unavailable] entry is still unsatisfiable -- the same installer
and repository files the image uses, with --assumeno.

Shares the OCI pull and repodata unpack with check-repo-availability.py; only
the final assertion differs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repodata import pinned_inputs, repository_metadata, unpack_metadata  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay", type=Path,
                        help="overlay manifest whose [unavailable] section is asserted")
    parser.add_argument("--engine", default="podman")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    overlay = args.overlay or root / "packages" / "utah.toml"
    base, packages = pinned_inputs(root / "Containerfile")
    print(f"Checking [unavailable] entries against {packages} on {base}", flush=True)
    with tempfile.TemporaryDirectory(prefix="utah-unavailable-") as tmp:
        repository_metadata(packages, Path(tmp))
        return subprocess.run([
            args.engine, "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{tmp}:/etc/utah-packages:ro,Z",
            "-v", f"{root / 'packages'}:/etc/yum.repos.d:ro,Z",
            "-v", f"{overlay.resolve()}:/tmp/utah.toml:ro,Z",
            "-v", f"{root / 'scripts/install-packages.py'}:/tmp/install-packages.py:ro,Z",
            base, "python3", "/tmp/install-packages.py",
            "--assert-unavailable", "/tmp/utah.toml",
        ], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
