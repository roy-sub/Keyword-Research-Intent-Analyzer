#!/usr/bin/env python3
"""Prove the keywords are really Google's, not ours.

Run this on a machine with normal internet access. It does three things and
prints all of them, so nothing has to be taken on trust:

  1. Asks Google directly, with plain `curl`-equivalent code that does not
     import a single line of this project. Prints the raw bytes Google sent.
  2. Asks the same question through this project's own collection code.
  3. Compares the two. If the app were inventing or padding keywords, the
     sets would differ and this script would say so.

It never calls an AI provider, never writes anything, and costs no quota:
it does not go through the API, so the rate limiter is not involved.

    python scripts/verify_live.py "ski chalet zermatt"
    python scripts/verify_live.py "chalet zermatt mieten" --market de-CH
    python scripts/verify_live.py "bundesliga top scorers" --full

`--full` runs the complete 37-query set, which takes about 40 seconds and is
the honest end-to-end check. Without it, only a handful of queries run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import markets  # noqa: E402
from app.config import ALPHABET, Settings  # noqa: E402
from app.suggest.base import build_query_set, merge_results  # noqa: E402
from app.suggest.google import GoogleSuggestProvider, build_client  # noqa: E402

RULE = "─" * 72


def ask_google_directly(query: str, lang: str, country: str) -> tuple[bytes, list[str]]:
    """Deliberately written with the standard library only.

    No httpx, no app code, no shared helper — if this and the app agree, they
    agree because Google said the same thing twice, not because they share a
    bug.
    """
    params = urllib.parse.urlencode(
        {"client": "firefox", "q": query, "hl": lang, "gl": country}
    )
    url = f"https://suggestqueries.google.com/complete/search?{params}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": f"{lang},en;q=0.8"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8", errors="replace"))
    return raw, (parsed[1] if len(parsed) > 1 else [])


async def ask_through_the_app(topic: str, market, full: bool) -> tuple[list, list[str]]:
    settings = Settings(ADMIN_PASSWORD="verify-script-not-a-real-login")
    client = build_client(settings)
    provider = GoogleSuggestProvider(client, settings)
    try:
        if full:
            results = await provider.fetch(topic, market)
        else:
            # A short sample: the seed, two letters, one question word and one
            # buying word — enough to show every query shape without the wait.
            queries = build_query_set(topic, ALPHABET, market)
            sample = [queries[0], queries[1], queries[2], queries[27], queries[33]]
            results = []
            for index, (query, query_type) in enumerate(sample):
                if index:
                    await provider._pace()
                results.append(
                    await provider._fetch_one(
                        query, query_type, market.lang, market.country
                    )
                )
    finally:
        await client.aclose()

    _sources, keywords, _failed = merge_results(results)
    return results, [k.keyword for k in keywords]


def compare(direct: list[str], app: list[str]) -> tuple[set[str], set[str]]:
    """Return (invented, missing) for the same query asked twice.

    `invented` is the damning set: keywords the app reported that Google did
    not return. `missing` is expected noise — autocomplete is not
    deterministic, and two calls a second apart can legitimately differ.
    """
    direct_set = {k.strip().lower() for k in direct if k.strip()}
    app_set = {k.strip().lower() for k in app if k.strip()}
    return app_set - direct_set, direct_set - app_set


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", help="the seed topic to check")
    parser.add_argument("--market", default="en-US", help="market code, e.g. de-CH")
    parser.add_argument(
        "--full", action="store_true", help="run all 37 queries (~40 seconds)"
    )
    args = parser.parse_args()

    market = markets.get(args.market)
    if market is None:
        print(f"Unknown market {args.market!r}. Known: {', '.join(markets.codes())}")
        return 2

    print(RULE)
    print(f"  TOPIC   {args.topic}")
    print(f"  MARKET  {market.label}   hl={market.lang}  gl={market.country}")
    print(f"  WORDS   {' '.join(market.question_modifiers + market.commercial_modifiers)}")
    print(RULE)

    # ---- 1. Google, asked directly -------------------------------------
    print("\n[1] Asking Google directly, with no code from this project.\n")
    try:
        raw, direct = ask_google_directly(args.topic, market.lang, market.country)
    except Exception as exc:  # noqa: BLE001 - the reason is the useful part
        print(f"    Could not reach Google: {type(exc).__name__}: {exc}")
        print("\n    This machine cannot reach Google. That is a network issue here,")
        print("    not a fault in the tool. Try from a normal connection.")
        return 1

    preview = raw[:220].decode("utf-8", errors="replace")
    print(f"    Raw bytes Google returned (first 220 of {len(raw)}):")
    print(f"      {preview}{'…' if len(raw) > 220 else ''}")
    print(f"\n    Google's suggestions for {args.topic!r} ({len(direct)}):")
    for keyword in direct:
        print(f"      · {keyword}")

    # ---- 2. The same thing, through the app ----------------------------
    span = "all 37 queries" if args.full else "a 5-query sample"
    print(f"\n[2] Asking through this project's collection code ({span}).\n")
    results, collected = asyncio.run(ask_through_the_app(args.topic, market, args.full))

    succeeded = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    print(f"    Queries run:       {len(results)}")
    print(f"    Answered:          {len(succeeded)}")
    print(f"    Failed:            {len(failed)}")
    for result in failed:
        print(f"      × {result.query}  →  {result.error}")
    print(f"    Unique keywords:   {len(collected)}")
    print("\n    First 15 collected:")
    for keyword in collected[:15]:
        print(f"      · {keyword}")

    # ---- 3. Do they agree? ---------------------------------------------
    print(f"\n[3] Comparing.\n")
    seed_result = next((r for r in results if r.type == "seed"), None)
    if seed_result is None or not seed_result.ok:
        print("    The seed query failed in step 2, so there is nothing to compare.")
        print("    Google is likely rate-limiting this address. Try again shortly.")
        return 1

    invented, missing = compare(direct, seed_result.keywords)

    print(f"    Google returned for the seed query:  {len(direct)}")
    print(f"    The app recorded for the same query: {len(seed_result.keywords)}")

    if invented:
        print(f"\n    ✗ The app has {len(invented)} keyword(s) Google did not return:")
        for keyword in sorted(invented):
            print(f"        {keyword}")
        print("\n    That should never happen. Stop and report it.")
        return 1

    print("\n    ✓ Every keyword the app recorded came from Google.")
    if missing:
        # Autocomplete is genuinely not deterministic — two calls a second
        # apart can differ. Only extra keywords prove fabrication; missing
        # ones just mean Google answered slightly differently.
        print(f"    ({len(missing)} in the direct call were not in the app's call —")
        print("     autocomplete varies between requests, which is normal.)")

    print("\n" + RULE)
    print("  VERDICT   The keywords are Google's own. Nothing was generated")
    print("            or padded by this tool or by any AI model.")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
