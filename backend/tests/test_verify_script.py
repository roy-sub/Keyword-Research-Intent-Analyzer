"""The live-verification script's comparison logic.

The script itself needs the internet, so what is tested here is the claim it
makes: that it would actually catch the app inventing a keyword. A comparison
that could never fail would prove nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_live import compare  # noqa: E402


def test_identical_sets_report_nothing():
    invented, missing = compare(["villa italy", "villa greece"], ["villa italy", "villa greece"])
    assert invented == set()
    assert missing == set()


def test_a_fabricated_keyword_is_caught():
    """The whole point: if the app returned something Google did not, say so."""
    invented, _missing = compare(["villa italy"], ["villa italy", "villa atlantis"])
    assert invented == {"villa atlantis"}


def test_a_keyword_google_returned_but_the_app_missed_is_not_an_accusation():
    """Autocomplete varies between calls; a missing keyword is not evidence
    of fabrication, so it must not land in `invented`."""
    invented, missing = compare(["villa italy", "villa greece"], ["villa italy"])
    assert invented == set()
    assert missing == {"villa greece"}


def test_comparison_ignores_casing_and_padding():
    invented, missing = compare(["Villa Italy"], ["  villa italy  "])
    assert invented == set()
    assert missing == set()


def test_empty_strings_are_not_treated_as_keywords():
    invented, missing = compare(["villa"], ["villa", "", "   "])
    assert invented == set()
    assert missing == set()


@pytest.mark.parametrize("direct,app,expected", [
    ([], [], set()),
    ([], ["anything"], {"anything"}),
    (["only google"], [], set()),
])
def test_edge_cases(direct, app, expected):
    invented, _missing = compare(direct, app)
    assert invented == expected
