# SPDX-License-Identifier: Apache-2.0
"""Check generated HTML links, fragments and required shared branding offline."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, path: Path):
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.brand = False
        self.feed(path.read_text(errors="replace"))

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        attrs = dict(attributes)
        if "id" in attrs and attrs["id"] is not None:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs and attrs[key] is not None:
                self.links.append(attrs[key])
        if tag == "link" and (attrs.get("href") or "").endswith("assets/brand.css"):
            self.brand = True
        assert attrs.get("data-md-color-primary") != "indigo", "Unexpected stock palette"


def verify(site: Path, *, require_reports: bool = False) -> int:
    root = site.resolve()
    pages = {p: Page(p) for p in root.rglob("*.html")}
    assert root / "index.html" in pages, "Missing landing page"
    assert (root / "build.json").is_file(), "Missing source identity"
    if require_reports:
        assert root / "reports" / "index.html" in pages, "Missing reports index page"
    for path, page in pages.items():
        rel = path.relative_to(root)
        is_docs_page = rel.parts[0] != "reports"
        if is_docs_page and path.name != "404.html":
            assert page.brand, f"Missing shared brand: {path}"
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc or link.startswith("/"):
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= "index.html"
            if not require_reports and target == root / "reports" / "index.html":
                continue
            assert target.exists(), f"{rel}: missing {link}"
            if url.fragment and target in pages:
                assert unquote(url.fragment) in pages[target].ids, f"{rel}: missing {link}"
    print(f"PASS: {len(pages)} HTML pages; local links/fragments and brand verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("artifacts/site"))
    parser.add_argument(
        "--require-reports",
        action="store_true",
        help="Fail unless reports/index.html and all report links are present.",
    )
    args = parser.parse_args(argv)
    return verify(args.site, require_reports=args.require_reports)


if __name__ == "__main__":
    raise SystemExit(main())
