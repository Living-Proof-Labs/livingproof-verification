# SPDX-License-Identifier: Apache-2.0
"""Stage authored pages and folded repository docs for Zensical.

The version page says ``development`` unless this build is a published release
whose tag resolves to the checkout's commit.
"""

import hashlib
import importlib.util
import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

SOURCE_REPO = "https://github.com/Living-Proof-Labs/livingproof-verification"


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def _demote_headings(text: str) -> str:
    """Shift Markdown ATX headings down one level outside fenced code blocks."""
    lines = []
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence and re.match(r"^#{1,5}\s", line):
            line = "#" + line
        lines.append(line)
    return "\n".join(lines).strip()


def _load_section(path: Path) -> str:
    text = path.read_text()
    text = re.sub(r"^\s*<!--\s*SPDX-License-Identifier:[^>]+-->\s*", "", text)
    return _demote_headings(text)


@lru_cache(maxsize=1)
def _site():
    """The site tool that already resolves a bundle tag to one commit."""
    path = Path(__file__).resolve().parents[1] / "site.py"
    spec = importlib.util.spec_from_file_location("verification_site", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load bundle tag resolver from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_label(sha: str, repository: Path, environ: Mapping[str, str] | None = None) -> str:
    """The version-page label for this docs build.

    A run that published a release (``RELEASE_PUBLISHED=true``) shows that release's
    tag when it is the bundle tag that resolves to ``sha`` in ``repository``. A rolling
    run stays ``development``, including when the commit already carries a tag, so it
    is not relabelled as stable. A tag for another commit, or a name that is not the
    plain ``vX.Y.Z`` the release published, stays ``development`` too.
    """
    env = os.environ if environ is None else environ
    if env.get("RELEASE_PUBLISHED") != "true":
        return "development"
    tag = env.get("RELEASE_TAG", "")
    if not tag or env.get("RELEASE_COMMIT") != sha:
        return "development"
    # The same resolution the report build logs: a plain vX.Y.Z pointing at this commit.
    if _site().bundle_tag(sha, repository) != tag:
        return "development"
    return tag


def version_markdown(sha: str, label: str) -> str:
    source = f"{SOURCE_REPO}/tree/{sha}"
    return (
        "# Version and source\n\n"
        f"Docs: {label} · [{sha[:12]}]({source})\n\n"
        f"Full source revision: `{sha}`.\n\n"
        "This site follows the built checkout. Verification bundle releases are tagged "
        "from verified `main` commits; historical releases remain available as "
        "[source tags](https://github.com/Living-Proof-Labs/livingproof-verification/tags).\n\n"
        "[Machine-readable build identity](build.json). Local uncommitted changes are not "
        "represented by the commit identity; use the CI artifact for review evidence.\n"
    )


def write_version_page(
    root: Path, sha: str, repository: Path, environ: Mapping[str, str] | None = None
) -> str:
    """Write ``version.md`` and return the label it shows."""
    label = release_label(sha, repository, environ)
    (root / "version.md").write_text(version_markdown(sha, label))
    print(f"Docs version: {label}")
    return label


def main() -> None:
    root = Path("artifacts/docs-source")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    shutil.copyfile("docs/index.md", root / "index.md")

    how_text = Path("docs/how-reports-work.md").read_text().rstrip()
    how_text += "\n\n" + _load_section(Path("docs/spec-provenance.md")) + "\n"
    (root / "how-reports-work.md").write_text(how_text)

    contribute_text = Path("docs/contribute.md").read_text().rstrip()
    for section_path in (
        Path("docs/robot-updates.md"),
        Path("docs/bundle-releases.md"),
        Path("docs/bundle-automation.md"),
    ):
        contribute_text += "\n\n" + _load_section(section_path)
    (root / "contribute.md").write_text(contribute_text + "\n")

    lock = json.loads(Path("docs-brand.lock.json").read_text())
    url = f"https://raw.githubusercontent.com/{lock['repository']}/{lock['commit']}/{lock['path']}"
    with urllib.request.urlopen(url, timeout=30) as response:
        brand = response.read()
    if hashlib.sha256(brand).hexdigest() != lock["sha256"]:
        raise ValueError("Shared brand checksum mismatch")
    (root / "assets").mkdir()
    (root / "assets/brand.css").write_bytes(brand)

    sha = output("git", "rev-parse", "HEAD")
    identity = {"source_commit": sha, "brand": lock}
    (root / "build.json").write_text(json.dumps(identity, indent=2) + "\n")
    # The checkout is the site job's, which fetched tags. The release outputs are the
    # process environment; a local build leaves them unset and stays development.
    write_version_page(root, sha, Path("."))

    license_text = Path("LICENSE").read_text()
    (root / "license.md").write_text(
        "# License\n\nRepository content is Apache-2.0. "
        "The fetched shared brand retains its upstream license notice.\n\n```text\n"
        + license_text
        + "\n```\n"
    )


if __name__ == "__main__":
    main()
