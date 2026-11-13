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
