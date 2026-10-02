#!/usr/bin/env python3
"""Digest-pinned OCI metadata reader shared by the package-availability checks.

A name lookup against Pages cannot verify the digest in Containerfile, library
dependencies, or packages supplied only by the base image's RPM database. The
availability checks therefore pull the pinned package image's repository metadata and
resolve the real install transaction against it. This module is the pull and
unpack; the callers decide what to resolve.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path


def verified_bytes(raw: bytes, digest: str) -> bytes:
    if digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ValueError(f"Registry content does not match {digest}")
    return raw


def pinned_inputs(containerfile: Path) -> tuple[str, str]:
    args = dict(re.findall(r"^ARG ([A-Z_]+)=(\S+)$", containerfile.read_text(), re.M))
    base = args["BASE_IMAGE"]
    packages = f"{args['PACKAGE_IMAGE']}@{args['PACKAGE_IMAGE_SHA']}"
    for image in (base, packages):
        if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}", image):
            raise ValueError(f"Expected a digest-pinned image, got {image!r}")
    return base, packages


def repository_metadata(image: str, destination: Path) -> None:
    registry, reference = image.split("/", 1)
    if registry != "ghcr.io":
        raise ValueError("The factory metadata reader currently supports ghcr.io images")
    repository, digest = reference.split("@", 1)
    query = urllib.parse.urlencode({"service": registry, "scope": f"repository:{repository}:pull"})
    with urllib.request.urlopen(f"https://{registry}/token?{query}", timeout=120) as response:
        token = json.load(response)["token"]

    def fetch(kind: str, digest: str) -> bytes:
        request = urllib.request.Request(
            f"https://{registry}/v2/{repository}/{kind}/{digest}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.oci.image.manifest.v1+json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return verified_bytes(response.read(), digest)

    manifest = json.loads(fetch("manifests", digest))
    # The factory publishes repodata first, separately from the large RPM
    # payload. Do not silently fall back to Pages or another image tag.
    layer = manifest["layers"][0]
    if layer["size"] > 64 * 1024 * 1024:
        raise ValueError("Package image lacks a small leading metadata layer; republish with repodata first")
    unpack_metadata(fetch("blobs", layer["digest"]), destination)


def unpack_metadata(raw: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for entry in archive:
            path = Path(entry.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe metadata path: {entry.name}")
            if entry.isdir():
                continue
            if not entry.isfile() or path.parts[:2] != ("repository", "repodata"):
                raise ValueError(f"Unexpected entry in metadata layer: {entry.name}")
            target = destination / Path(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(entry).read())
    if not (destination / "repodata/repomd.xml").is_file():
        raise ValueError("Pinned metadata layer contains no repomd.xml")
