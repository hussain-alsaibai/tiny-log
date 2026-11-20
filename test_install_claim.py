"""The install line must name an install that actually resolves.

`test_llms_drift.py` asserts the *header* of llms.txt and stayed green while
the one line an agent acts on said `pip install tiny-config` -- a name that is
not on PyPI at all. The README was right the whole time; only the file written
for the agent reader was wrong.

Online check (resolves the name against the live index) is opt-in:

    NETWORK_TESTS=1 python -m pytest test_install_claim.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from install_claim import (
    assert_install_lines_are_honest,
    assert_name_is_ours_if_published,
)

ROOT = Path(__file__).parent
PROJECT = "tiny-log"


def test_install_lines_name_an_install_we_own():
    assert_install_lines_are_honest(ROOT, PROJECT)


def test_pypi_name_is_ours_if_it_resolves():
    """No-op unless NETWORK_TESTS=1. Catches a squatted name going live."""
    assert_name_is_ours_if_published(PROJECT, expected_summary_hint="")
