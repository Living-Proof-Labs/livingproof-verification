# SPDX-License-Identifier: Apache-2.0
"""Replace published docs while retaining the report history and site marker."""

import argparse
from pathlib import Path
import shutil


def assemble(docs: Path, site: Path) -> None:
    if not (docs / "index.html").is_file():
        raise ValueError(f"Build the documentation first: {docs}/index.html is missing")
    if any((docs / name).exists() for name in ("reports", "site.json")):
        raise ValueError("Documentation must not replace reports or site.json")
    site.mkdir(parents=True, exist_ok=True)
    for path in site.iterdir():
        if path.name in {"reports", "site.json"}:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(docs, site, dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, default=Path("artifacts/site"))
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.docs, args.site)
