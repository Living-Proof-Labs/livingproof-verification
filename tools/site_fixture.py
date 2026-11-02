# SPDX-License-Identifier: Apache-2.0
"""Fixture capability reports for a site build with no CI run behind it, and a link check.

`write DIR` writes one report artifact per locked definition and configuration, in the
reporter's layout (report.json/.html/.md/.txt and SHA256SUMS). `check SITE` fails when a
page links a local file that is not in the site. The PR checks job builds a site from these
fixtures with `tools/site.py build` and checks it; nothing is published.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("site_tool", Path(__file__).with_name("site.py"))
site_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_tool)


def write(directory: Path) -> list[Path]:
    written = []
    for target in site_tool.targets():
        folder = directory / target["artifact"]
        folder.mkdir(parents=True)
        report = {
            "report_version": "1.0.0",
            "summaries": [
                {
                    "capability": "walk-hallway",
                    "system": {"id": f"fixture/{target['artifact']}"},
                    "grade": "FAIL",
                    "k": 12,
                    "n": 20,
                    "required_passes": 18,
                }
            ],
        }
        (folder / "report.json").write_text(json.dumps(report))
        for name in ("report.html", "report.md", "report.txt"):
            (folder / name).write_text(f"fixture {name}\n")
        (folder / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
                for n in site_tool.REPORT_FILES
            )
        )
        written.append(folder)
    return written


def broken_links(site: Path) -> list[str]:
    broken = []
    pages = list(site.rglob("*.html"))
    if not (site / "index.html").is_file():
        broken.append("index.html missing")
    for page in pages:
        for href in re.findall(r"""(?:href|src)=["']([^"'#]+)""", page.read_text()):
            if re.match(r"[a-z]+:", href):
                continue
            path = page.parent / href
            if href.endswith("/"):
                path = path / "index.html"
            if not path.exists():
                broken.append(f"{page.relative_to(site)}: {href}")
    return broken


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("write").add_argument("directory", type=Path)
    sub.add_parser("check").add_argument("site", type=Path)
    args = parser.parse_args(argv)
    if args.command == "write":
        for folder in write(args.directory):
            print(f"Wrote {folder}")
        return 0
    broken = broken_links(args.site)
    for line in broken:
        print(f"broken link: {line}", file=sys.stderr)
    pages = len(list(args.site.rglob("*.html")))
    print(f"{pages} pages, {len(broken)} broken links")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
