#!/usr/bin/env python3
"""Resolve Utah's real install transaction against its pinned OCI inputs.

A name lookup against Pages cannot verify the digest in Containerfile, library
dependencies, or packages supplied only by the base image's RPM database.
Use the same installer and repository files as the image, with --assumeno.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Sibling import: repodata.py holds the OCI pull and repodata unpack shared by
# both availability checks. Add scripts/ to the path so the import resolves
# whether this file runs as a script or a test loader execs it in isolation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from repodata import pinned_inputs, repository_metadata, unpack_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("overlay", type=Path, nargs="?", default=None)
    parser.add_argument("--engine", default="podman")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    overlay = args.overlay or args.manifest.with_name("utah.toml")
    base, packages = pinned_inputs(root / "Containerfile")
    print(f"Checking package repository {packages} on {base}", flush=True)
    with tempfile.TemporaryDirectory(prefix="utah-repodata-") as tmp:
        repository_metadata(packages, Path(tmp))
        return subprocess.run([
            args.engine, "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{tmp}:/etc/utah-packages:ro,Z",
            "-v", f"{root / 'packages'}:/etc/yum.repos.d:ro,Z",
            "-v", f"{args.manifest.resolve()}:/tmp/bluefin.toml:ro,Z",
            "-v", f"{overlay.resolve()}:/tmp/utah.toml:ro,Z",
            "-v", f"{root / 'scripts/install-packages.py'}:/tmp/install-packages.py:ro,Z",
            base, "python3", "/tmp/install-packages.py", "--resolve",
            "/tmp/bluefin.toml", "/tmp/utah.toml",
        ], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
