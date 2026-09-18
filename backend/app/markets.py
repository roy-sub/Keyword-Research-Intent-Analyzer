"""Markets: a language and country, with the modifier words that suit them.

A market is the whole locale bundle, not just two ISO codes. Changing
`hl`/`gl` without changing the modifier words is the trap this module exists
to close: asking Google for `how ferienwohnung zermatt` with `hl=de` returns
almost nothing, because German speakers do not type "how". The question and
buying words have to move with the language or the run quietly loses a third
of its queries.

Each market keeps the same **shape** — the seed alone, the seed plus each
letter a-z, six question words and four buying words, 37 queries in total —
so the two markets are directly comparable and every downstream estimate
(progress, run timeout, expected counts) holds without special-casing.

Adding a market is one entry here. Nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    """One language/country pairing and the modifier words that suit it."""

    code: str
    label: str
    lang: str
    country: str
    question_modifiers: tuple[str, ...]
    commercial_modifiers: tuple[str, ...]
    # Named in the prompt so the model knows what language it is reading and
    # does not mistake German compounds for noise.
    language_name: str

    @property
    def expected_queries(self) -> int:
        return 1 + 26 + len(self.question_modifiers) + len(self.commercial_modifiers)

    def as_dict(self) -> dict:
        """What the frontend needs to render the selector and the run facts."""
        return {
            "code": self.code,
            "label": self.label,
            "lang": self.lang,
            "country": self.country,
            "question_modifiers": list(self.question_modifiers),
            "commercial_modifiers": list(self.commercial_modifiers),
            "expected_queries": self.expected_queries,
        }


MARKETS: dict[str, Market] = {
    "en-US": Market(
        code="en-US",
        label="English · United States",
        lang="en",
        country="us",
        question_modifiers=("who", "what", "when", "where", "why", "how"),
        # "near me" is the one suffix in the set: English puts it after the
        # noun, and `near me chalet` is not a search anyone performs.
        commercial_modifiers=("best", "buy", "cheap", "near me"),
        language_name="English",
    ),
    "de-CH": Market(
        code="de-CH",
        label="Deutsch · Schweiz",
        lang="de",
        country="ch",
        # The six W-words from the client's own briefing. "welche" replaces a
        # direct "which", and there is no natural German equivalent of "when"
        # that produces useful autocomplete, so "wann" is left out in favour
        # of the briefing's list.
        question_modifiers=("wer", "wie", "was", "wo", "warum", "welche"),
        # Also the briefing's: "beste" and "mieten" are the two highest-volume
        # commercial prefixes in Swiss German search, and "buy"/"best" are
        # kept because Swiss results are routinely bilingual.
        commercial_modifiers=("beste", "mieten", "buy", "best"),
        language_name="German",
    ),
    "de-DE": Market(
        code="de-DE",
        label="Deutsch · Deutschland",
        lang="de",
        country="de",
        question_modifiers=("wer", "wie", "was", "wo", "warum", "welche"),
        commercial_modifiers=("beste", "kaufen", "günstig", "mieten"),
        language_name="German",
    ),
    "en-GB": Market(
        code="en-GB",
        label="English · United Kingdom",
        lang="en",
        country="gb",
        question_modifiers=("who", "what", "when", "where", "why", "how"),
        commercial_modifiers=("best", "buy", "cheap", "near me"),
        language_name="English",
    ),
}

# Suffix modifiers, by market. Everything else is a prefix, which is how the
# client's own reference code builds its queries (`f"{q} {base_keyword}"`).
_SUFFIX_MODIFIERS = {"near me", "in der nähe"}


def is_suffix(modifier: str) -> bool:
    return modifier.strip().lower() in _SUFFIX_MODIFIERS


def get(code: str) -> Market | None:
    """Case-insensitive lookup, so `de-ch` and `de-CH` both resolve."""
    if not code:
        return None
    wanted = code.strip().lower()
    for market in MARKETS.values():
        if market.code.lower() == wanted:
            return market
    return None


def codes() -> list[str]:
    return list(MARKETS)


def listing() -> list[dict]:
    return [m.as_dict() for m in MARKETS.values()]
