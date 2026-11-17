#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Resolve one reporter/verifier release bundle; publish files only after full validation.

Examples: python3 tools/update_bundle.py --reporter v3.11.2
          python3 tools/update_bundle.py --verifier v1.25.4
No latest lookup: all other inputs must resolve to their existing identities.
"""

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.request

import lock
import update_verifier

REPORTER = "Living-Proof-Labs/livingproof-capability-report"
SETTINGS = Path("verifications/settings.toml")
WHEELS = Path("tools/install_reporter.py")
CURRENT_TESTS = (Path("tests/test_corner.py"), Path("tests/test_site.py"))


def paths(root):
    return [
        Path(".github/workflows/verify.yml"),
        Path(".devcontainer/devcontainer.json"),
        Path("README.md"),
        Path(".github/requirements/lock-check-git.txt"),
        Path(".github/requirements/lock-check.in"),
        SETTINGS,
        WHEELS,
        *CURRENT_TESTS,
        *[p.relative_to(root) for p in lock.definition_paths(root)],
        *[lock.lock_path(p).relative_to(root) for p in lock.definition_paths(root)],
    ]


def release_wheel(tag):
    """Compare downloaded bytes with GitHub's published asset digest; never execute a wheel."""
    name = f"livingproof_capability_report-{tag[1:]}-py3-none-any.whl"
    url = f"https://api.github.com/repos/{REPORTER}/releases/tags/{tag}"
    with urllib.request.urlopen(url, timeout=120) as response:
        release = json.load(response)
    if release["tag_name"] != tag or release["draft"] or release["prerelease"]:
        raise lock.LockError("reporter must be a published stable release")
    assets = [a for a in release["assets"] if a["name"] == name]
    if len(assets) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", assets[0].get("digest") or ""):
        raise lock.LockError("release wheel needs exactly one published sha256 digest")
    url = f"https://github.com/{REPORTER}/releases/download/{tag}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response:
        digest = hashlib.sha256(response.read()).hexdigest()
    if "sha256:" + digest != assets[0]["digest"]:
        raise lock.LockError("downloaded reporter wheel differs from published digest")
    return digest


def replace_once(path, pattern, replacement):
    text, count = re.subn(pattern, lambda _: replacement, path.read_text(), flags=re.M)
    if count != 1:
        raise lock.LockError(f"{path.name}: expected one current release expectation")
    path.write_text(text)


def wheel_pin(path, tag, digest):
    text = path.read_text()
    tree = ast.parse(text)
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "REPORTER_WHEELS" for t in n.targets)
    )
    wheels = ast.literal_eval(node.value)
    version = tag[1:]
    if version in wheels:
        if wheels[version] != digest:
            raise lock.LockError("published wheel changed for an already recorded release")
        return
    lines = text.splitlines(keepends=True)
    lines.insert(node.end_lineno - 1, f'    "{version}": "{digest}",\n')
    path.write_text("".join(lines))


def unrelated(document, reporter, verifier):
    result = copy.deepcopy(document)
    if reporter:
        result["releases"].pop("reporter")
    if verifier:
        result["releases"].pop("verifier")
        result["evaluators"].pop(lock.EVALUATOR)
    return result


def current_expectations(stage, releases, evaluator):
    """Retain literal, independent assertions; only named current-version constants move.

    Frozen robot/spec/world fixtures, evidence digests and assertion bodies are never edited.
    """
    for name, pin in (
        ("VERIFIER", releases["verifier"]),
        ("EVALUATOR", evaluator),
        ("REPORTER", releases["reporter"]),
    ):
        replace_once(
            stage / CURRENT_TESTS[0],
            rf'^{name}_RELEASE = \("v\d+\.\d+\.\d+", "[0-9a-f]{{40}}"\)$',
            f'{name}_RELEASE = ("{pin["tag"]}", "{pin["commit"]}")',
        )
    replace_once(
        stage / CURRENT_TESTS[1],
        r'^    assert reporter_tag == "v\d+\.\d+\.\d+"$',
        f'    assert reporter_tag == "{releases["reporter"]["tag"]}"',
    )


def update(root, *, reporter=None, verifier=None):
    if not reporter and not verifier:
        raise lock.LockError("select --reporter vX.Y.Z and/or --verifier vX.Y.Z")
    for tag in (reporter, verifier):
        if tag is not None and not lock.TAG.fullmatch(tag):
            raise lock.LockError("release must be vX.Y.Z")
    files = paths(root)
    if any((root / p).is_symlink() for p in files):
        raise lock.LockError("bundle files must not be symlinks")
    original = {p: (root / p).read_bytes() for p in files}
    with tempfile.TemporaryDirectory(prefix="bundle-update-") as directory:
        stage = Path(directory)
        for path, data in original.items():
            (stage / path).parent.mkdir(parents=True, exist_ok=True)
            (stage / path).write_bytes(data)
        if verifier:
            update_verifier.update(stage, verifier)
        if reporter:
            setting = lock.load_reporter_setting(stage / SETTINGS)
            if setting["repository"] != REPORTER:
                raise lock.LockError("unexpected reporter repository")
            text = (stage / SETTINGS).read_text()
            old = f'tag = "{setting["tag"]}"'
            if text.count(old) != 1:
                raise lock.LockError("expected one reporter tag")
            (stage / SETTINGS).write_text(text.replace(old, f'tag = "{reporter}"'))
            wheel_pin(stage / WHEELS, reporter, release_wheel(reporter))
        # Full fresh resolution, not only a projection of old locks. No dependency code runs.
        generated = []
        for definition in lock.definition_paths(stage):
            result, _ = lock.generate(definition, stage / "work" / definition.stem)
            path = lock.lock_path(definition).relative_to(stage)
            before = json.loads(original[path])
            if unrelated(result, reporter, verifier) != unrelated(before, reporter, verifier):
                raise lock.LockError(f"{path}: unrelated inputs drifted; review separately")
            if reporter and result["releases"]["reporter"]["tag"] != reporter:
                raise lock.LockError("resolved reporter differs from requested release")
            if verifier and result["releases"]["verifier"]["tag"] != verifier:
                raise lock.LockError("resolved verifier differs from requested release")
            (stage / path).write_text(json.dumps(result, indent=2) + "\n")
            generated.append(result)
        releases = generated[0]["releases"]
        evaluator = generated[0]["evaluators"][lock.EVALUATOR]
        for result in generated:
            if any(result["releases"][name] != releases[name] for name in ("reporter", "verifier")):
                raise lock.LockError("release resolution changed between definitions")
            if result["evaluators"][lock.EVALUATOR] != evaluator:
                raise lock.LockError("evaluator resolution changed between definitions")
        current_expectations(stage, releases, evaluator)
        changed = [p for p in files if (stage / p).read_bytes() != original[p]]
        if any((root / p).read_bytes() != original[p] for p in files):
            raise lock.LockError("checkout changed during resolution; no files published")
        # Resolution/validation is complete. Roll back ordinary write failures as well.
        written = []
        try:
            for path in changed:
                written.append(path)
                (root / path).write_bytes((stage / path).read_bytes())
        except OSError:
            for path in written:
                (root / path).write_bytes(original[path])
            raise
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reporter")
    parser.add_argument("--verifier")
    args = parser.parse_args()
    print("Updated: " + ", ".join(map(str, update(lock.repo_root(), **vars(args)))))


if __name__ == "__main__":
    main()
