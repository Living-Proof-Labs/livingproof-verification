# SPDX-License-Identifier: Apache-2.0
"""Check #138 in Chromium against synthetic fixtures; retain JSON and PNG evidence.

Run after site_fixture.py write and site.py build:
  python tools/site_browser.py SITE OUTPUT
Requires .github/requirements/browser.txt and `python -m playwright install chromium`.
This changes only fixture run metadata, never published reports or deployment state.
"""

import argparse
from datetime import datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import threading
import traceback

from playwright.sync_api import expect, sync_playwright

from site_fixture import site_tool


SEEDED_RUNS = ("8000000001", "8000000002", "8000000003")


def seed_history(site, path):
    """Three older runs behind a run, the oldest on an earlier spec: one `Spec changed` row.

    Copies keep the run's published files; only run.json and its identity change (#146).
    """
    folder = path.parent
    record = json.loads(path.read_text())
    created = datetime.fromisoformat(record["run"]["created"].replace("Z", "+00:00"))
    for index, run_id in enumerate(SEEDED_RUNS, start=1):
        copy = folder.parent / run_id
        shutil.copytree(folder, copy)
        older = json.loads(json.dumps(record))
        older["run"]["id"] = run_id
        older["run"]["url"] = older["run"]["url"].rsplit("/", 1)[0] + "/" + run_id
        older["run"]["created"] = (created - timedelta(days=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if index == len(SEEDED_RUNS):
            specs = older["releases"]["specs"]
            specs["url"] = specs["url"].replace(f"/{specs['tag']}", "/v0.0.1")
            specs["tag"] = "v0.0.1"
        (copy / "run.json").write_text(json.dumps(older) + "\n")


def prepare(site):
    # Exercise a PASS badge alongside FAIL badges/chips with the real renderer.
    records = sorted(site.glob("*/*/runs/*/run.json"))
    assert records, "Build the fixture site first"
    for index, path in enumerate(records):
        record = json.loads(path.read_text())
        record["outcome"][0]["grade"] = "PASS" if index == 0 else "FAIL"
        if index:
            record["failing_evaluators"] = [
                {"id": "fixture", "title": "Fixture evaluator", "href": "#fixture"}
            ]
        path.write_text(json.dumps(record) + "\n")
    for path in records:
        seed_history(site, path)
    site_tool.render(site, now="2026-11-19T08:00:00Z")
    return {
        str(path.parents[2].relative_to(site)) + "/latest/": str(path.parent.relative_to(site))
        + "/"
        for path in records
    }


# A commit id is a link, never text (#146): 7 to 40 hex characters with a letter in a-f.
COMMIT_ID = re.compile(r"\b(?=[0-9]*[a-f])[0-9a-f]{7,40}\b")


def check_no_commit_id(page, scope="body"):
    """The text a reader sees, in `scope`, holds no commit-id-like word."""
    found = COMMIT_ID.findall(page.locator(scope).first.inner_text())
    assert not found, f"commit id visible as text in {scope} of {page.url}: {found}"


def check_commit_link_matches_report_link(page):
    """#146 (Art): a row's "commit" link looks like the "Report" link beside it."""
    row = page.locator(".row", has=page.locator("a.release.is-fallback")).first
    commit = row.locator("a.release.is-fallback")
    report = row.locator("a", has_text="Report").first
    expect(commit).to_have_text("commit")
    props = "e=>{const c=getComputedStyle(e);return [c.textDecorationLine,c.color,c.fontWeight]}"
    found, expected = commit.evaluate(props), report.evaluate(props)
    assert found == expected, f"commit link {found} differs from the Report link {expected}"
    assert found[0] == "underline", f"commit link is not underlined: {found}"


def check_report(page, width, result, output):
    """#146: the report's provenance is collapsed, below the result, in sentence case."""
    page.locator(".row a", has_text="Report").first.click()
    block = page.locator("details.spec-provenance")
    expect(block).to_have_count(1)
    assert block.get_attribute("open") is None, "the provenance block starts collapsed"
    summary = block.locator("summary")
    expect(summary).to_have_css("text-transform", "none")
    expect(summary).to_have_css("letter-spacing", "normal")
    expect(summary).to_contain_text("Spec v")
    text = summary.inner_text()
    assert text != text.upper(), f"summary is shouted: {text!r}"
    summary.click()
    assert block.get_attribute("open") is not None
    check_no_commit_id(page, "details.spec-provenance")
    expect(block.locator("a").first).to_have_attribute("href", re.compile(r"^https://"))
    result.setdefault("report", {})[str(width)] = {"summary": text, "commit_ids_visible": 0}
    page.screenshot(path=output / f"{width}-report-provenance.png")


def check_history(page, width, height, result, output):
    """#146: rows carry no spec provenance; the header names the specs; one row flags a change."""
    check_no_commit_id(page)
    expect(page.locator(".masthead")).not_to_contain_text("@")
    check_commit_link_matches_report_link(page)
    expect(page.locator("main .spec-provenance")).to_have_count(0)
    expect(page.locator("main")).not_to_contain_text("Spec provenance")
    expect(page.locator(".masthead")).to_contain_text("specs v")
    expect(page.locator(".chip-spec")).to_have_count(1)
    expect(page.locator(".chip-spec")).to_contain_text("Spec changed: v0.0.1 → ")
    rows = page.locator(".row")
    assert rows.count() == len(SEEDED_RUNS) + 1
    visible = sum(
        1 for index in range(rows.count()) if rows.nth(index).bounding_box()["y"] + 60 < height
    )
    if width == 390:
        assert visible >= 3, f"only {visible} history rows start on the first screen at 390"
    result.setdefault("history", {})[str(width)] = {
        "rows_on_first_screen": visible,
        "spec_changed_chips": 1,
    }
    page.screenshot(path=output / f"{width}-history.png")
    check_report(page, width, result, output)


def check_viewport(browser, url, output, width, height, result, destinations):
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=True)
    page = context.new_page()
    page.set_default_timeout(10000)
    result.update(viewport=[width, height], status="running", navigations=[])
    try:
        for grade, selector in [("PASS", ".card:has(.badge-pass)"), ("FAIL", ".card.is-fail")]:
            page.goto(url)
            card = page.locator(selector).first
            latest = card.locator(".card-link").get_attribute("href")
            history = card.locator(".history-link").get_attribute("href")
            assert latest == history + "latest/"
            check_no_commit_id(page)
            expect(card.locator("a")).to_have_count(2)
            title = card.locator(".title").inner_text()
            expect(card.locator(".card-link")).to_have_attribute(
                "aria-label", f"{title}, latest report ({grade})"
            )
            for target in ["body", ".badge", ".title", ".release", "time"] + (
                [".chip"] if grade == "FAIL" else []
            ):
                page.goto(url)
                card = page.locator(selector).first
                card.scroll_into_view_if_needed()
                box = (card if target == "body" else card.locator(target)).bounding_box()
                assert box
                x = box["x"] + (box["width"] - 8 if target == "body" else box["width"] / 2)
                y = box["y"] + (box["height"] - 8 if target == "body" else box["height"] / 2)
                # Real hit testing: do not force locator clicks through the stretched overlay.
                if width == 390:
                    page.touchscreen.tap(x, y)
                else:
                    page.mouse.click(x, y)
                expect(page).to_have_url(url + destinations[latest])
                result["navigations"].append(
                    {
                        "grade": grade,
                        "target": target,
                        "href": latest,
                        "destination": destinations[latest],
                    }
                )
            page.goto(url)
            history_link = page.locator(selector).first.locator(".history-link")
            history_link.scroll_into_view_if_needed()
            box = history_link.bounding_box()
            assert box and (width != 390 or box["height"] >= 44)
            result.setdefault("history_heights", {})[grade] = box["height"]
            history_link.tap() if width == 390 else history_link.click()
            expect(page).to_have_url(url + history)
            check_history(page, width, height, result, output)

        page.goto(url)
        page.mouse.move(0, 0)
        card = page.locator(".card").first
        # Reach the first card from the document start, without programmatic focus.
        for _ in range(30):
            page.keyboard.press("Tab")
            if page.locator(":focus").get_attribute("class") == "card-link":
                break
        expect(card.locator(".card-link")).to_be_focused()
        expect(card).to_have_css("outline-style", "solid")
        expect(card).to_have_css("outline-width", "3px")
        expect(card).to_have_css("outline-color", "rgb(14, 135, 133)")
        expect(card.locator(".card-link")).to_have_css("outline-style", "none")
        page.screenshot(path=output / f"{width}-focus.png")
        page.keyboard.press("Tab")
        expect(card.locator(".history-link")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator(".card-link").nth(1)).to_be_focused()
        result["tab_order"] = ["card", "History", "next card"]
        result["focus_ring"] = "solid 3px rgb(14, 135, 133)"

        page.goto(url)
        card = page.locator(".card").first
        card.hover()
        # Web-first assertions wait for the real CSS transition to finish.
        expect(card).to_have_css("background-color", "rgb(232, 238, 245)")
        expect(card).to_have_css("border-top-color", "rgb(195, 206, 219)")
        expect(card).to_have_css("cursor", "pointer")
        page.screenshot(path=output / f"{width}-hover.png")
        result["screenshots"] = [f"{width}-focus.png", f"{width}-hover.png"]
        result["status"] = "passed"
    except Exception:
        page.screenshot(path=output / f"{width}-failure.png")
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = {
        "status": "failed",
        "head_sha": os.environ.get("COMMIT", "local"),
        "checkout_sha": os.environ.get("GITHUB_SHA", "local"),
        "playwright": importlib.metadata.version("playwright"),
        "fixture": "synthetic PASS and FAIL records; not deployed",
        "viewports": [],
    }
    try:
        destinations = prepare(args.site)
        with ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(args.site.resolve()))
        ) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    results["chromium"] = browser.version
                    for width, height in [(390, 844), (1280, 800)]:
                        result = {}
                        results["viewports"].append(result)
                        check_viewport(
                            browser,
                            f"http://127.0.0.1:{server.server_port}/",
                            args.output,
                            width,
                            height,
                            result,
                            destinations,
                        )
                    browser.close()
                results["status"] = "passed"
            finally:
                server.shutdown()
                thread.join()
    except Exception:
        results["error"] = traceback.format_exc()
    finally:
        encoded = json.dumps(results, indent=2) + "\n"
        (args.output / "results.json").write_text(encoded)
        print(encoded)
    return 0 if results["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
