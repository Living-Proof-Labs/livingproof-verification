# SPDX-License-Identifier: Apache-2.0
"""The manual's version page names a release only when this run published it (#193).

A release run whose tag resolves to the built commit renders that tag on version.html
(``Docs: v1.9.7 · <short>``). A rolling run stays labelled development, including when
the commit already carries a tag, so it is not relabelled as stable.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

import pytest
import yaml
from test_bundle_release import commit, git, site_job_checkout

ROOT = Path(__file__).parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())

spec = importlib.util.spec_from_file_location("docs_build", ROOT / "tools/docs/build.py")
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)

_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def render_version_html(markdown: str) -> str:
    """The paragraphs Zensical writes for version.md.

    Inline code becomes ``<code>`` and a markdown link becomes ``<a href>``. That is the
    version.html paragraph the live manual shows, for example
    ``<p>Docs: development · <a href=".../tree/<sha>"><short></a></p>``.
    """
    parts = []
    for block in markdown.strip().split("\n\n"):
        if block.startswith("# "):
            continue
        html = _CODE.sub(r"<code>\1</code>", block)
        html = _LINK.sub(r'<a href="\2">\1</a>', html)
        parts.append(f"<p>{html}</p>")
    return "\n".join(parts) + "\n"


def docs_paragraph(label: str, sha: str) -> str:
    href = f"https://github.com/Living-Proof-Labs/livingproof-verification/tree/{sha}"
    return f'<p>Docs: {label} · <a href="{href}">{sha[:12]}</a></p>'


def version_html(tmp_path: Path, repository: Path, sha: str, env: dict) -> str:
    """Run the docs build's version step and render the version.html it publishes."""
    source = tmp_path / "docs-source"
    source.mkdir(parents=True)
    docs.write_version_page(source, sha, repository, env)
    page = tmp_path / "version.html"
    page.write_text(render_version_html((source / "version.md").read_text()))
    return page.read_text()


def release_env(sha: str, tag: str = "v1.9.7", *, published: str = "true") -> dict:
    return {"RELEASE_PUBLISHED": published, "RELEASE_TAG": tag, "RELEASE_COMMIT": sha}


def tagged_checkout(tmp_path: Path, message: str = "feat: release the manual") -> tuple[Path, str]:
    """The site job's detached shallow checkout of one annotated release commit."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    sha = commit(origin, message)
    git(origin, "tag", "-a", "v1.9.7", "-m", "release")
    checkout = site_job_checkout(origin, sha, tmp_path / "site-job")
    return checkout, sha


def stage_manual(checkout: Path, monkeypatch) -> None:
    """The files ``tools/docs/build.py`` reads, with the brand fetch stubbed out."""
    for relative in (
        "docs/index.md",
        "docs/how-reports-work.md",
        "docs/spec-provenance.md",
        "docs/contribute.md",
        "docs/robot-updates.md",
        "docs/bundle-releases.md",
        "docs/bundle-automation.md",
        "LICENSE",
    ):
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    brand = b"/* brand */\n"
    (checkout / "docs-brand.lock.json").write_text(
        json.dumps(
            {
                "repository": "Living-Proof-Labs/brand",
                "commit": "a" * 40,
                "path": "brand.css",
                "sha256": hashlib.sha256(brand).hexdigest(),
            }
        )
    )

    class Response:
        def read(self) -> bytes:
            return brand

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.chdir(checkout)
    monkeypatch.setattr(docs.urllib.request, "urlopen", lambda *args, **kwargs: Response())


def rendered_manual(checkout: Path) -> str:
    """version.html from the markdown the docs build just staged."""
    markdown = (checkout / "artifacts/docs-source/version.md").read_text()
    page = checkout / "artifacts/site/version.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render_version_html(markdown))
    return page.read_text()


def test_a_release_run_docs_build_renders_the_tag_on_version_html(tmp_path, monkeypatch):
    """published == true and the tag resolves to this commit: version.html names the tag."""
    checkout, sha = tagged_checkout(tmp_path)
    stage_manual(checkout, monkeypatch)
    for key, value in release_env(sha).items():
        monkeypatch.setenv(key, value)
    docs.main()
    page = rendered_manual(checkout)
    assert docs_paragraph("v1.9.7", sha) in page
    assert "Docs: development" not in page
    assert f"<code>{sha}</code>" in page


@pytest.mark.parametrize("published", [None, "false", ""])
def test_a_non_release_run_renders_development_on_version_html(tmp_path, monkeypatch, published):
    """A rolling run stays development even when the commit already carries the release tag."""
    checkout, sha = tagged_checkout(tmp_path)
    stage_manual(checkout, monkeypatch)
    for name in ("RELEASE_PUBLISHED", "RELEASE_TAG", "RELEASE_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    if published is not None:
        for key, value in release_env(sha, published=published).items():
            monkeypatch.setenv(key, value)
    docs.main()
    page = rendered_manual(checkout)
    assert docs_paragraph("development", sha) in page
    assert "Docs: v1.9.7" not in page
    assert "v1.9.7" not in page


def test_a_published_tag_for_another_commit_stays_development(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    tagged = commit(origin, "feat: release the manual")
    git(origin, "tag", "-a", "v1.9.7", "-m", "release")
    later = commit(origin, "chore: rolling commit")
    checkout = site_job_checkout(origin, later, tmp_path / "site-job")
    page = version_html(tmp_path / "other", checkout, later, release_env(later))
    assert docs_paragraph("development", later) in page
    assert "v1.9.7" not in page
    assert tagged != later


def test_a_published_prerelease_or_unresolved_tag_stays_development(tmp_path):
    checkout, sha = tagged_checkout(tmp_path)
    prerelease = version_html(tmp_path / "pre", checkout, sha, release_env(sha, tag="v1.9.7-rc.1"))
    assert docs_paragraph("development", sha) in prerelease
    assert "v1.9.7-rc.1" not in prerelease
    other = version_html(tmp_path / "missing", checkout, sha, release_env(sha, tag="v9.9.9"))
    assert docs_paragraph("development", sha) in other
    assert "v9.9.9" not in other
    mismatch = version_html(tmp_path / "commit", checkout, sha, release_env("a" * 40))
    assert docs_paragraph("development", sha) in mismatch


def test_release_label_reads_the_process_environment(tmp_path, monkeypatch):
    """The docs build, with no explicit mapping, reads the workflow's release outputs."""
    checkout, sha = tagged_checkout(tmp_path)
    monkeypatch.setenv("RELEASE_PUBLISHED", "true")
    monkeypatch.setenv("RELEASE_TAG", "v1.9.7")
    monkeypatch.setenv("RELEASE_COMMIT", sha)
    assert docs.release_label(sha, checkout) == "v1.9.7"
    monkeypatch.setenv("RELEASE_PUBLISHED", "false")
    assert docs.release_label(sha, checkout) == "development"


def test_the_docs_build_reads_the_release_outputs_on_every_site_run():
    """Rolling runs still build the manual; the same step reads the release when one published."""
    step = next(
        item
        for item in WORKFLOW["jobs"]["site"]["steps"]
        if item.get("name") == "Build and verify the docs site"
    )
    assert "if" not in step
    assert step["env"] == {
        "RELEASE_PUBLISHED": "${{ needs.release.outputs.published }}",
        "RELEASE_TAG": "${{ needs.release.outputs.git-tag }}",
        "RELEASE_COMMIT": "${{ needs.release.outputs.commit }}",
    }
    assert "task docs:build" in step["run"]
