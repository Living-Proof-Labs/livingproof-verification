# SPDX-License-Identifier: Apache-2.0
"""Stage authored pages and folded repository docs for Zensical."""

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path


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
source = f"https://github.com/Living-Proof-Labs/livingproof-verification/tree/{sha}"
identity = {"source_commit": sha, "brand": lock}
(root / "build.json").write_text(json.dumps(identity, indent=2) + "\n")
(root / "version.md").write_text(
    "# Version and source\n\n"
    f"Docs: development · [{sha[:12]}]({source})\n\n"
    f"Full source revision: `{sha}`.\n\n"
    "This site follows the built checkout. Verification bundle releases are tagged "
    "from verified `main` commits; historical releases remain available as "
    "[source tags](https://github.com/Living-Proof-Labs/livingproof-verification/tags).\n\n"
    "[Machine-readable build identity](build.json). Local uncommitted changes are not "
    "represented by the commit identity; use the CI artifact for review evidence.\n"
)

license_text = Path("LICENSE").read_text()
(root / "license.md").write_text(
    "# License\n\nRepository content is Apache-2.0. "
    "The fetched shared brand retains its upstream license notice.\n\n```text\n"
    + license_text
    + "\n```\n"
)
