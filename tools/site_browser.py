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
    # Exercise FAIL and PASS badges, newest-first Needs attention, evaluator chips, and findings.
    records = sorted(site.glob("*/*/runs/*/run.json"))
    assert records, "Build the fixture site first"
    fail_specs = {
        "booster-t1/stock-mujoco-hallway": (
            "2026-11-19T07:30:00Z",
            [
                {"id": "livingproof-scenario", "title": "Scenario", "href": "#evaluator-scenario"},
                {"id": "aaron-roller", "title": "Aaron Roller", "href": "#evaluator-aaron"},
            ],
            "Left the hallway through the right wall at 8.56 s.",
        ),
        "unitree-g1/stock-mujoco-hallway": (
            "2026-11-19T07:50:00Z",
            [
                {
                    "id": "livingproof-scenario",
                    "title": "Scenario",
                    "href": "#evaluator-scenario",
                    "summary": "Drifted 0.66 m from the centreline, then touched the wall.",
                }
            ],
            "Drifted 0.66 m from the centreline, then touched the wall.",
        ),
    }
    for path in records:
        key = str(path.parents[2].relative_to(site))
        record = json.loads(path.read_text())
        if key in fail_specs:
            created, evaluators, summary = fail_specs[key]
            record["outcome"][0]["grade"] = "FAIL"
            record["run"]["created"] = created
            record["failing_evaluators"] = [
                {**evaluators[0], "summary": summary},
                *evaluators[1:],
            ]
        else:
            record["outcome"][0]["grade"] = "PASS"
            record["run"]["created"] = "2026-11-19T07:40:00Z"
            record.pop("failing_evaluators", None)
        path.write_text(json.dumps(record) + "\n")
    for path in records:
        seed_history(site, path)
    site_tool.render(site, now="2026-11-19T08:00:00Z")

    # Synthetic all-pass + 15-scenario robot page to verify the all-pass state and +n dot truncation.
    all_pass_history = {}
    for (robot, variant), recs in site_tool.runs(site).items():
        cloned = json.loads(json.dumps(recs))
        for rec in cloned:
            rec["outcome"][0]["grade"] = "PASS"
            rec.pop("failing_evaluators", None)
        all_pass_history[(robot, variant)] = cloned
    sample_variant, sample_recs = next(
        (v, r) for (rob, v), r in all_pass_history.items() if rob == "unitree-g1"
    )
    for extra in range(11):
        extra_variant = f"stock-mujoco-extra-{extra:02d}"
        cloned = json.loads(json.dumps(sample_recs))
        cloned[0]["outcome"][0]["capability"] = f"extra-scenario-{extra:02d}"
        all_pass_history[("unitree-g1", extra_variant)] = cloned
    (site / "all-pass.html").write_text(
        site_tool._index_page_html(all_pass_history, "2026-11-19T08:00:00Z")
    )
    for name, grade in [("insufficient", "INSUFFICIENT_EVIDENCE"), ("unknown", "UNKNOWN")]:
        mixed = {key: json.loads(json.dumps(recs)) for key, recs in site_tool.runs(site).items()}
        for recs in mixed.values():
            recs[0]["outcome"][0]["grade"] = "PASS"
            recs[0].pop("failing_evaluators", None)
        mixed[("booster-t1", "stock-mujoco-hallway")][0]["outcome"][0]["grade"] = grade
        (site / f"{name}.html").write_text(
            site_tool._index_page_html(mixed, "2026-11-19T08:00:00Z")
        )
    (site / "empty.html").write_text(site_tool._index_page_html({}, "2026-11-19T08:00:00Z"))

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


def _open_robot(page, width, robot_id="booster-t1"):
    details = page.locator(f"details#{robot_id}")
    if details.get_attribute("open") is None:
        summary = details.locator("summary")
        summary.scroll_into_view_if_needed()
        summary.tap() if width == 390 else summary.click()
        expect(details).to_have_attribute("open", "")
    return details


def check_viewport(browser, url, output, width, height, result, destinations):
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=True)
    context.clock.install(time="2026-11-19T07:50:30Z")
    page = context.new_page()
    page.set_default_timeout(10000)
    result.update(viewport=[width, height], status="running", navigations=[])
    try:
        # 1. Default collapsed state + Needs attention (#139) + client-side relative time (#163)
        page.goto(url)
        check_no_commit_id(page)
        robots = page.locator("details.robot")
        expect(robots).to_have_count(3)
        for index in range(robots.count()):
            assert robots.nth(index).get_attribute("open") is None
        needs_cards = page.locator(".needs .card")
        expect(needs_cards).to_have_count(2)
        expect(needs_cards.nth(0).locator(".sub")).to_have_text("Unitree G1 · stock · MuJoCo")
        expect(needs_cards.nth(1).locator(".sub")).to_have_text("Booster T1 · stock · MuJoCo")
        expect(needs_cards.nth(1).locator(".chip")).to_have_count(2)
        expect(needs_cards.nth(1).locator(".chip").nth(0)).to_have_text("Scenario")
        expect(needs_cards.nth(1).locator(".chip").nth(1)).to_have_text("Aaron Roller")
        expect(needs_cards.nth(0).locator(".finding")).to_contain_text("Drifted 0.66 m")
        g1_time = needs_cards.nth(0).locator("time")
        expect(g1_time).to_have_attribute("datetime", "2026-11-19T07:50:00Z")
        expect(g1_time.locator(".rel-time")).to_contain_text("just now")
        expect(g1_time.locator(".abs-time")).to_have_text("19 Nov 2026, 18:50 AEDT")
        expect(page.locator("details#unitree-g1 > summary .visually-hidden")).to_have_text(
            "Unitree G1, 4 scenarios, 1 failed, 3 passed, latest commit, "
            "just now · 19 Nov 2026, 18:50 AEDT"
        )
        expect(page.locator("details#unitree-g1 > summary .summary-row")).to_have_attribute(
            "aria-hidden", "true"
        )
        page.screenshot(path=output / f"{width}-collapsed.png", full_page=True)

        # Live refresh across the 60-second boundary while the page stays open (#163).
        context.clock.run_for(30000)
        expect(g1_time).to_have_attribute("datetime", "2026-11-19T07:50:00Z")
        expect(g1_time.locator(".rel-time")).to_contain_text("1 minute ago")
        expect(g1_time.locator(".abs-time")).to_have_text("19 Nov 2026, 18:50 AEDT")
        expect(page.locator("details#unitree-g1 > summary .visually-hidden")).to_have_text(
            "Unitree G1, 4 scenarios, 1 failed, 3 passed, latest commit, "
            "1 minute ago · 19 Nov 2026, 18:50 AEDT"
        )
        result["live_clock_boundary"] = {
            "initial_30s": "just now · 19 Nov 2026, 18:50 AEDT",
            "after_60s": "1 minute ago · 19 Nov 2026, 18:50 AEDT",
        }

        # 2. Hash anchor opens target robot section; cards inside are fail-first, then alphabetical.
        page.goto(url + "#booster-t1")
        expect(page.locator("details#booster-t1")).to_have_attribute("open", "")
        expect(page.locator("details#unitree-g1")).not_to_have_attribute("open", "")
        booster_cards = page.locator("details#booster-t1 .card")
        expect(booster_cards).to_have_count(3)
        expect(booster_cards.nth(0).locator(".badge")).to_have_text("FAIL")
        expect(booster_cards.nth(0).locator(".sub")).to_have_text("stock · MuJoCo")
        expect(booster_cards.nth(1).locator(".badge")).to_have_text("PASS")
        expect(booster_cards.nth(2).locator(".badge")).to_have_text("PASS")
        page.locator("details#booster-t1 > summary").scroll_into_view_if_needed()
        page.screenshot(path=output / f"{width}-expanded.png")

        # 3. #138 card hit testing for PASS (inside expanded robot) and FAIL (in Needs attention).
        for grade, selector, needs_expand in [
            ("PASS", "details#booster-t1 .card:has(.badge-pass)", True),
            ("FAIL", ".needs .card.is-fail", False),
        ]:
            page.goto(url)
            if needs_expand:
                _open_robot(page, width, "booster-t1")
            card = page.locator(selector).first
            latest = card.locator(".card-link").get_attribute("href")
            history = card.locator(".history-link").get_attribute("href")
            assert latest == history + "latest/"
            expect(card.locator("a")).to_have_count(2)
            title = card.locator(".title").inner_text()
            expect(card.locator(".card-link")).to_have_attribute(
                "aria-label", f"{title}, latest report ({grade})"
            )
            for target in ["body", ".badge", ".title", ".release", "time"] + (
                [".chip"] if grade == "FAIL" else []
            ):
                page.goto(url)
                if needs_expand:
                    _open_robot(page, width, "booster-t1")
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
            if needs_expand:
                _open_robot(page, width, "booster-t1")
            history_link = page.locator(selector).first.locator(".history-link")
            history_link.scroll_into_view_if_needed()
            box = history_link.bounding_box()
            assert box and (width != 390 or box["height"] >= 44)
            result.setdefault("history_heights", {})[grade] = box["height"]
            history_link.tap() if width == 390 else history_link.click()
            expect(page).to_have_url(url + history)
            check_history(page, width, height, result, output)

        # 4. Keyboard Tab order: Needs attention cards -> summary -> Enter opens -> card -> History.
        page.goto(url)
        page.mouse.move(0, 0)
        card = page.locator(".card").first
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
        for _ in range(10):
            page.keyboard.press("Tab")
            is_summary = page.evaluate(
                "document.activeElement && document.activeElement.tagName === 'SUMMARY'"
            )
            if is_summary:
                break
        first_summary = page.locator("details#booster-t1 > summary")
        expect(first_summary).to_be_focused()
        expect(first_summary).to_have_css("outline-style", "solid")
        expect(first_summary).to_have_css("outline-width", "3px")
        expect(first_summary).to_have_css("outline-color", "rgb(14, 135, 133)")
        page.keyboard.press("Enter")
        expect(page.locator("details#booster-t1")).to_have_attribute("open", "")
        page.keyboard.press("Tab")
        expect(page.locator("details#booster-t1 .card-link").first).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("details#booster-t1 .history-link").first).to_be_focused()
        result["tab_order"] = ["needs card", "History", "summary", "Enter", "robot card", "History"]
        result["focus_ring"] = "solid 3px rgb(14, 135, 133)"

        # 5. Hover state on card
        page.goto(url)
        card = page.locator(".card").first
        card.hover()
        expect(card).to_have_css("background-color", "rgb(232, 238, 245)")
        expect(card).to_have_css("border-top-color", "rgb(195, 206, 219)")
        expect(card).to_have_css("cursor", "pointer")
        page.screenshot(path=output / f"{width}-hover.png")

        # 6. All-pass state & 390 dot-strip +n truncation
        page.goto(url + "all-pass.html")
        expect(page.locator("#needs-attention")).to_have_count(0)
        expect(page.locator(".all-pass")).to_have_text(
            re.compile(r"^All \d+ latest results pass\.$")
        )
        if width == 390:
            more = page.locator("details#unitree-g1 .dots .pip-more")
            expect(more).to_be_visible()
            expect(more).to_have_text(re.compile(r"^\+\d+$"))
            overflows = page.evaluate(
                """() => {
                    const s = document.querySelector('details#unitree-g1 .dots');
                    return s.scrollWidth > s.clientWidth + 1;
                }"""
            )
            assert not overflows, "dot strip overflowed at 390"
            result["dot_truncation"] = more.inner_text()
        page.screenshot(path=output / f"{width}-all-pass.png", full_page=True)

        # No FAIL does not imply PASS: retain truthful counts for other outcomes.
        for name, label in [("insufficient", "insufficient evidence"), ("unknown", "unknown")]:
            page.goto(url + f"{name}.html")
            expect(page.locator(".all-pass")).to_have_count(0)
            expect(page.locator("main > .status")).to_have_text(
                f"10 latest results: 9 passed, 1 {label}."
            )
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.screenshot(path=output / f"{width}-{name}.png", full_page=True)
        page.goto(url + "empty.html")
        expect(page.locator(".all-pass")).to_have_count(0)
        expect(page.locator("main > .status")).to_have_text(
            "No verification reports published yet."
        )
        result["nonpassing_and_empty"] = "passed"

        result["screenshots"] = [
            f"{width}-collapsed.png",
            f"{width}-expanded.png",
            f"{width}-focus.png",
            f"{width}-hover.png",
            f"{width}-all-pass.png",
            f"{width}-insufficient.png",
            f"{width}-unknown.png",
        ]
        result["status"] = "passed"
    except Exception:
        page.screenshot(path=output / f"{width}-failure.png")
        raise
    finally:
        context.close()


def check_timestamps_no_js_and_print(browser, url, output, results):
    """#163: verify reopening an old page, no-JavaScript browsing, and print/PDF exports."""
    evidence = {}

    # 1. Reopening an old page days and months later without rebuilding.
    reopen_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        reopen_ctx.clock.install(time="2027-01-03T07:50:00Z")
        page = reopen_ctx.new_page()
        page.goto(url)
        first_time = page.locator(".needs .card time").first
        expect(first_time).to_have_attribute("datetime", "2026-11-19T07:50:00Z")
        expect(first_time).to_have_text("19 Nov 2026, 18:50 AEDT")
        expect(page.locator(".rel-time")).to_have_count(0)
        expect(page.locator("details#unitree-g1 > summary .visually-hidden")).to_have_text(
            "Unitree G1, 4 scenarios, 1 failed, 3 passed, latest commit, 19 Nov 2026, 18:50 AEDT"
        )
        body_text = page.locator("body").inner_text().lower()
        assert "just now" not in body_text and "ago" not in body_text
        evidence["reopen_45_days_later"] = "19 Nov 2026, 18:50 AEDT"
    finally:
        reopen_ctx.close()

    # 2. No-JavaScript context: absolute Australia/Sydney timestamps remain readable everywhere.
    no_js_ctx = browser.new_context(
        viewport={"width": 1280, "height": 800}, java_script_enabled=False
    )
    try:
        page = no_js_ctx.new_page()
        for target_path in ("", "unitree-g1/stock-mujoco-hallway/"):
            page.goto(url + target_path)
            first_time = page.locator("time").first
            expect(first_time).to_have_attribute("datetime", "2026-11-19T07:50:00Z")
            expect(first_time).to_have_text("19 Nov 2026, 18:50 AEDT")
            text = page.locator("body").inner_text().lower()
            assert "just now" not in text and "minutes ago" not in text
        evidence["no_javascript"] = "19 Nov 2026, 18:50 AEDT"
    finally:
        no_js_ctx.close()

    # 3. Print media emulation and PDF export for index and history.
    print_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        print_ctx.clock.install(time="2026-11-19T07:50:30Z")
        page = print_ctx.new_page()
        page.goto(url + "#booster-t1")
        expect(page.locator("details#booster-t1")).to_have_attribute("open", "")
        page.emulate_media(media="print")
        expect(page.locator(".rel-time").first).to_be_hidden()
        expect(page.locator(".abs-time").first).to_be_visible()
        assert page.locator(".needs .card time").first.inner_text() == "19 Nov 2026, 18:50 AEDT"
        page.screenshot(path=output / "1280-print-index.png", full_page=True)
        index_pdf = output / "index-print.pdf"
        page.pdf(path=index_pdf, format="A4")
        assert index_pdf.is_file() and index_pdf.read_bytes().startswith(b"%PDF-")

        page.goto(url + "unitree-g1/stock-mujoco-hallway/")
        page.emulate_media(media="print")
        expect(page.locator(".rel-time").first).to_be_hidden()
        expect(page.locator(".abs-time").first).to_be_visible()
        assert page.locator(".row time").first.inner_text() == "19 Nov 2026, 18:50 AEDT"
        page.screenshot(path=output / "1280-print-history.png", full_page=True)
        history_pdf = output / "history-print.pdf"
        page.pdf(path=history_pdf, format="A4")
        assert history_pdf.is_file() and history_pdf.read_bytes().startswith(b"%PDF-")
        evidence["print_and_pdf"] = [
            "1280-print-index.png",
            "1280-print-history.png",
            "index-print.pdf",
            "history-print.pdf",
        ]
    finally:
        print_ctx.close()

    results["timestamp_checks"] = evidence


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
                    base_url = f"http://127.0.0.1:{server.server_port}/"
                    for width, height in [(390, 844), (1280, 800)]:
                        result = {}
                        results["viewports"].append(result)
                        check_viewport(
                            browser,
                            base_url,
                            args.output,
                            width,
                            height,
                            result,
                            destinations,
                        )
                    check_timestamps_no_js_and_print(browser, base_url, args.output, results)
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
