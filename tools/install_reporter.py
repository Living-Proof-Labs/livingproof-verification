#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Install the reporter release named in verifications/settings.toml.

The wheel hash is checked before install. A tag with no hash here is refused.
``--venv DIR`` installs into that virtualenv; otherwise the install is ``pip --user``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "verifications" / "settings.toml"
# livingproof-capability-report release wheels, keyed by version (the tag without v).
REPORTER_WHEELS = {
    "3.6.0": "c600c502f6e8722c358264cf9f104bba759188334e6329821b463fbaab862824",
    "3.7.0": "e02bd0c8f553a8da0745ea2f6312bb27c59aeef9dcdc7a16673fd9aba77c3d5c",
    "3.8.2": "d3f97b70ae2310928819fbdbccf02ce53bc227e86dd71bca5478b07cf65f9d01",
    "3.8.4": "3d56a28184c80c5d7dff7e8bc78e1dc0a5b5c0591cbbee511d57ab2fcb8a708e",
    "3.8.7": "90935b34db175a7e71e5f5fc844fecb344662a19271fe9ab6a1676d2b7179de9",
    "3.9.0": "2111e1bb21d9c321a9f36da7ed8cf8224d82980d5ce51155b15239ef5010f711",
    "3.10.1": "af820dc5e7c8c86a8fd111efb58ee653f4f29af07fba6e7352716ac284ab3fe2",
    "3.11.1": "0bcbffd826549962e940fb35750533061f6acabec0808db571205058cdb70964",
    "3.11.2": "eb432701fa530496ed8c414b850bd065cf680fe1f71c592be2b96608c6018ba5",
    "3.11.3": "3b347b331fe8a0f9292c902340fe39ac6f2c31f155dc123ad838eb1a2ce8a4b3",
    "3.11.6": "e97e7ca73ab191e684b51bc714aa5b010338dcb92ea8f81b70efffd2744b8d3c",
    "3.12.1": "2d75e0c37a542c00abe264e591ebbdeda6a5275777be944fdf21d4cc98ab211e",
    "3.12.5": "2b3a4d62977e67c047cc6112e4ab328a77ed0e7644c57507797f3e57113fecbc",
    "3.13.0": "a586369780ad59955678fcd32a9e002f7f530ec77317a2213c97e582895c6088",
    "3.13.2": "6957d3e542e2d9b67dd5c0a1edbb8c32c7f53223389b17e8bc545c94c6880ac7",
}
COMMON_VERSION = "1.0.0"
COMMON_SHA256 = "8b7795748b54eeb89610f3716245f45db48ac0474465a5abbf974e7075c1476c"


def _download(url: str, destination: Path, digest: str) -> None:
    destination.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit(f"{destination.name}: sha256 is {actual}, expected {digest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--venv", type=Path, help="virtualenv to install into")
    args = parser.parse_args(argv)
    tag = tomllib.loads(SETTINGS.read_text())["reporter"]["tag"]
    version = tag.removeprefix("v")
    digest = REPORTER_WHEELS.get(version)
    if digest is None:
        known = ", ".join(sorted(REPORTER_WHEELS))
        raise SystemExit(f"no wheel hash for reporter {tag} (known: {known})")
    reporter_name = f"livingproof_capability_report-{version}-py3-none-any.whl"
    common_name = f"livingproof_common-{COMMON_VERSION}-py3-none-any.whl"
    base = "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/download"
    common_url = (
        "https://github.com/Living-Proof-Labs/livingproof-common/releases/download/"
        f"v{COMMON_VERSION}/{common_name}"
    )
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        reporter = folder / reporter_name
        common = folder / common_name
        _download(f"{base}/{tag}/{reporter_name}", reporter, digest)
        _download(common_url, common, COMMON_SHA256)
        if args.venv:
            env = os.environ.copy()
            env["VIRTUAL_ENV"] = str(args.venv)
            command = ["uv", "pip", "install", str(common), str(reporter)]
        else:
            env = os.environ
            command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--user",
                str(common),
                str(reporter),
            ]
        subprocess.run(command, check=True, env=env)
    print(f"installed livingproof-capability-report {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
