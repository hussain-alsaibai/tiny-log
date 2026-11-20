"""Shared doc-guard: an install line must name an install that resolves.

The 2026-11-20 audit found `llms.txt` telling agents `pip install tiny-config`
and `pip install tiny-validator` while **neither name exists on PyPI at all**,
and the same file claiming tiny-cli was installable under its own name when
that name belongs to an unrelated genomic-tooling CLI (0.1.0, TinyBio LLC).

The README of every one of those repos was correct: ``pip install
git+https://github.com/...``. So the repository knew, and the file written for
the *agent reader* was wrong -- the reader least able to notice.

The version drift guard did not catch it because it only asserted the header.
This guard asserts the part a user acts on.

Why the network check is opt-in
-------------------------------
A test that calls pypi.org fails when CI has no network, and a test that fails
for an unrelated reason gets deleted. So:

  * offline (always, in CI): a bare ``pip install <name>`` is only allowed
    when the file carries an explicit collision note (a PyPI URL for the
    stranger, or an UNPUBLISHED marker) on the install line or within the
    note block that follows it.

    The name appearing in ``pyproject.toml`` is **not** evidence. That is the
    claim under suspicion: a repo can declare ``name = "tiny-config"`` while
    PyPI hands that name to somebody else, and the first draft of this very
    guard passed on the broken files because it treated the declaration as
    proof.
  * online (``NETWORK_TESTS=1``): the name is resolved against the live index
    and must belong to *this* project.

Run with ``NETWORK_TESTS=1`` when you cut a release.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

BARE_INSTALL = re.compile(
    r"pip install\s+(?!git\+)(?!--)([A-Za-z][A-Za-z0-9_.\-]*)"
)

NETWORK = os.environ.get("NETWORK_TESTS") == "1"


def assert_install_lines_are_honest(root: Path, project_name: str) -> None:
    """Assert every plain `pip install X` for this project names a real package.

    Raises AssertionError with the offending line so the failure reads as the
    bug rather than as a missing fixture.
    """
    llms = root / "llms.txt"
    assert llms.is_file(), f"{llms} is missing"

    text = llms.read_text(encoding="utf-8")
    declared = _pyproject_names(root)
    assert project_name in declared, (
        f"project name {project_name!r} is not in pyproject.toml ({declared})"
    )

    lines = text.splitlines()
    offenders = []
    for lineno, line in enumerate(lines, 1):
        for name in BARE_INSTALL.findall(line):
            # `pip install tiny-log[redact]` -> "tiny-log" (extras already stripped)
            if name not in declared:
                offenders.append((lineno, name, line.strip(), "not a project name"))
                continue
            if _has_collision_note(lines, lineno):
                continue
            offenders.append(
                (lineno, name, line.strip(), "no collision/unpublished note")
            )

    assert not offenders, (
        "llms.txt tells an agent to `pip install` a name this project has not "
        "proven it owns on PyPI:\n"
        + "\n".join(f"  line {n}: {name} ({why})  <- {line}"
                    for n, name, line, why in offenders)
        + "\n\nEither use the git+https install line, or put an explicit note "
        "(a pypi.org/project/ URL for the stranger, an 'unpublished' marker, "
        "or a 'do not run pip install <name>' warning) within a few lines."
    )


NEGATION = re.compile(
    r"\b(do not|don't|never|must not|avoid)\b.{0,40}?pip install"
    r"|pip install.{0,40}?\b(is|would fetch|belongs? to)\b",
    re.I,
)


def _has_collision_note(lines: list[str], lineno: int, window: int = 4) -> bool:
    """A note around the install line that names the collision.

    The window was 6 and only forward, which produced two opposite bugs in
    two drafts:

      * too wide -- it reached past ``- Docs:`` into the next section, so
        tiny-cli's *correct* file passed because text eleven lines away
        merely mentioned pypi.org;
      * forward only -- the corrected tiny-config/tiny-log wording puts the
        warning ("this project is unpublished on PyPI") *above* the line and
        quotes the bad command below it, so the fix failed its own test.

    Widening is not the answer; a note that is not adjacent is not a note.
    Look a few lines each way and require a collision or a negation.
    """
    start = max(0, lineno - 1 - window)
    end = lineno - 1 + window
    chunk = " ".join(lines[start:end]).lower()
    says_collision = "pypi.org/project" in chunk or "unpublished" in chunk
    return says_collision or bool(NEGATION.search(chunk))


def _pyproject_names(root: Path) -> set[str]:
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    names = set(re.findall(r'^name\s*=\s*"([^"]+)"', text, re.M))
    assert names, "pyproject.toml has no [project].name"
    return {n.lower() for n in names}


def assert_name_is_ours_if_published(project_name: str, expected_summary_hint: str) -> None:
    """Online only: resolve the name on PyPI and check it is this project."""
    if not NETWORK:
        return
    url = f"https://pypi.org/pypi/{project_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as fh:  # noqa: S310
            info = json.load(fh)["info"]
    except urllib.error.HTTPError as exc:
        assert exc.code == 404, f"PyPI returned {exc.code} for {project_name}"
        return  # 404 == the name is free, which is consistent with "unpublished"
    summary = (info.get("summary") or "").lower()
    assert expected_summary_hint.lower() in summary, (
        f"PyPI name {project_name!r} exists but belongs to a different project: "
        f"{info.get('summary')!r}"
    )
