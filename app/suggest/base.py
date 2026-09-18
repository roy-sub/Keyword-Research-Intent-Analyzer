"""Provider-agnostic contract for keyword collection.

Routes depend on `SuggestProvider`, never on Google specifically, so a paid
provider (SerpApi, DataForSEO, ...) can be dropped in later by implementing
this one method and swapping the instance built in the lifespan handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.models import KeywordEntry, SourceGroup, SourceType


@dataclass
class SourceResult:
    """The outcome of a single autocomplete query.

    A failed query is returned as a `SourceResult` with `error` set and no
    keywords, rather than being dropped — errors are never swallowed, and the
    caller turns these into `failed_queries` in the API response.
    """

    query: str
    type: SourceType
    keywords: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@runtime_checkable
class SuggestProvider(Protocol):
    async def fetch(self, topic: str, lang: str, country: str) -> list[SourceResult]:
        """Run the full query set for `topic` and return one result per query."""
        ...


def build_query_set(
    topic: str,
    alphabet: list[str],
    question_modifiers: list[str],
    commercial_modifiers: list[str],
) -> list[tuple[str, SourceType]]:
    """The seed alone, seed + each letter, then the modifier expansions.

    Question modifiers are prefixes ("how <topic>"), commercial modifiers are
    handled by position: "near me" reads naturally as a suffix, the rest as
    prefixes.
    """
    seed = topic.strip()
    queries: list[tuple[str, SourceType]] = [(seed, "seed")]
    queries += [(f"{seed} {letter}", "alphabet") for letter in alphabet]
    queries += [(f"{modifier} {seed}", "question") for modifier in question_modifiers]
    for modifier in commercial_modifiers:
        if modifier.startswith("near"):
            queries.append((f"{seed} {modifier}", "commercial"))
        else:
            queries.append((f"{modifier} {seed}", "commercial"))
    return queries


def dedupe_keywords(raw: list[str]) -> list[str]:
    """Case-insensitive dedupe preserving the first occurrence's casing.

    Whitespace is stripped and empties dropped.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def merge_results(results: list[SourceResult]) -> tuple[list[SourceGroup], list[KeywordEntry], list[str]]:
    """Fold per-query results into the API's `sources` / `keywords` / failures.

    `sources` keeps every successful query and its own keywords intact — this
    is the client's verification data. `keywords` is the deduplicated union,
    sorted alphabetically, each entry carrying every query and type that
    produced it.
    """
    groups: list[SourceGroup] = []
    failed: list[str] = []

    # keyed by casefolded keyword -> (display casing, ordered source queries, ordered types)
    merged: dict[str, tuple[str, list[str], list[SourceType]]] = {}

    for result in results:
        if not result.ok:
            failed.append(result.query)
            continue
        keywords = dedupe_keywords(result.keywords)
        groups.append(SourceGroup(query=result.query, type=result.type, keywords=keywords))
        for keyword in keywords:
            key = keyword.casefold()
            if key not in merged:
                merged[key] = (keyword, [], [])
            _, sources, types = merged[key]
            if result.query not in sources:
                sources.append(result.query)
            if result.type not in types:
                types.append(result.type)

    entries = [
        KeywordEntry(keyword=display, sources=sources, types=types)
        for display, sources, types in merged.values()
    ]
    entries.sort(key=lambda e: (e.keyword.casefold(), e.keyword))
    return groups, entries, failed
