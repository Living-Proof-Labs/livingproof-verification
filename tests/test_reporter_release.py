# SPDX-License-Identifier: Apache-2.0
"""The reporter release named in settings.toml, checked against what GitHub and ghcr publish.

Vera's review checks for a reporter bump, automated: the recorded wheel sha256 is the
published asset's digest and its SHA256SUMS line, the release is published and stable,
every lock's reporter commit is the tag's commit, and the locked image digest is the
multi-arch index (linux/amd64 and linux/arm64), not one platform's manifest.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import tomllib
import urllib.request

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "install_reporter_tool", ROOT / "tools/install_reporter.py"
)
install_reporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_reporter)
REPORTER = tomllib.loads((ROOT / "verifications/settings.toml").read_text())["reporter"]
LOCKS = sorted((ROOT / "verifications").glob("*.lock.json"))
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
network = pytest.mark.skipif(
    not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1"
)


def test_the_settings_tag_has_a_recorded_wheel_hash():
    version = REPORTER["tag"].removeprefix("v")
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", install_reporter.REPORTER_WHEELS[version])


def _get(url, accept="application/vnd.github+json", token=None):
    headers = {"Accept": accept}
    token = token or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read(), r.headers


def _api(path):
    return json.loads(_get(f"https://api.github.com/repos/{REPORTER['repository']}/{path}")[0])


@network
def test_recorded_wheel_hash_is_the_published_asset():
    tag = REPORTER["tag"]
    version = tag.removeprefix("v")
    release = _api(f"releases/tags/{tag}")
    assert release["draft"] is False and release["prerelease"] is False
    assets = {asset["name"]: asset for asset in release["assets"]}
    wheel = f"livingproof_capability_report-{version}-py3-none-any.whl"
    recorded = install_reporter.REPORTER_WHEELS[version]
    assert assets[wheel]["digest"] == f"sha256:{recorded}"
    sums = _get(assets["SHA256SUMS"]["browser_download_url"], accept="*/*")[0].decode()
    assert f"{recorded}  {wheel}" in sums.splitlines()


@network
def test_every_lock_pins_the_tag_commit_and_the_multi_arch_index():
    tag = REPORTER["tag"]
    ref = _api(f"git/ref/tags/{tag}")["object"]
    if ref["type"] == "tag":
        ref = _api(f"git/tags/{ref['sha']}")["object"]
    name = REPORTER["image"].removeprefix("ghcr.io/")
    token = json.loads(_get(f"https://ghcr.io/token?scope=repository:{name}:pull")[0])["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{name}/manifests/{tag.removeprefix('v')}",
        headers={"Authorization": f"Bearer {token}", "Accept": ", ".join(sorted(INDEX_TYPES))},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        digest = response.headers["Docker-Content-Digest"]
        index = json.loads(response.read())
    assert index["mediaType"] in INDEX_TYPES
    platforms = {(m["platform"]["os"], m["platform"]["architecture"]) for m in index["manifests"]}
    assert {("linux", "amd64"), ("linux", "arm64")} <= platforms
    assert LOCKS
    for path in LOCKS:
        pin = json.loads(path.read_text())["releases"]["reporter"]
        assert (pin["tag"], pin["commit"], pin["digest"]) == (tag, ref["sha"], digest), path.name
