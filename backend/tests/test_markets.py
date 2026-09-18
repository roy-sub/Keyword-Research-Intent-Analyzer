"""Markets: the locale picker, and the guarantees that make it safe.

The point of a market is that language and modifier words move together. A
test suite that only checked `hl` and `gl` would pass while the run quietly
fired English question words at German searches, so most of what is asserted
here is about the modifier words travelling with the locale.
"""

from __future__ import annotations

import pytest

from app import markets
from app.cache import TTLCache
from app.config import Settings


def make_settings(**overrides) -> Settings:
    base = {"ADMIN_PASSWORD": "test-password"}
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------- registry


def test_the_two_contracted_markets_exist():
    """en-US is what shipped; de-CH is what the client's briefing assumed."""
    assert markets.get("en-US") is not None
    assert markets.get("de-CH") is not None


def test_lookup_is_case_insensitive():
    """A market code travels through a URL and a JSON body; casing varies."""
    assert markets.get("de-ch") is markets.get("de-CH")
    assert markets.get("DE-CH") is markets.get("de-CH")


def test_unknown_market_resolves_to_none_not_an_error():
    assert markets.get("zz-ZZ") is None
    assert markets.get("") is None


def test_german_market_carries_the_briefings_own_modifier_words():
    """These six W-words are lifted verbatim from the client's document."""
    de = markets.MARKETS["de-CH"]
    assert de.lang == "de"
    assert de.country == "ch"
    assert de.question_modifiers == ("wer", "wie", "was", "wo", "warum", "welche")
    assert "beste" in de.commercial_modifiers
    assert "mieten" in de.commercial_modifiers


@pytest.mark.parametrize("code", markets.codes())
def test_no_market_mixes_languages_in_its_question_words(code):
    """A German market must not carry English question words, or vice versa."""
    market = markets.MARKETS[code]
    english = {"who", "what", "when", "where", "why", "how"}
    german = {"wer", "wie", "was", "wo", "warum", "welche", "wann"}
    words = set(market.question_modifiers)
    if market.lang == "de":
        assert not (words & english), f"{code} leaks English question words"
    if market.lang == "en":
        assert not (words & german), f"{code} leaks German question words"


@pytest.mark.parametrize("code", markets.codes())
def test_every_market_is_the_same_size(code):
    """37 queries everywhere, so the two markets stay comparable and the
    progress estimate and run timeout need no per-market special case."""
    assert markets.MARKETS[code].expected_queries == 37


# ---------------------------------------------------------------- settings


def test_both_markets_are_enabled_by_default():
    assert [m.code for m in make_settings().enabled_markets] == ["en-US", "de-CH"]


def test_default_market_is_preselected():
    assert make_settings().default_market.code == "de-CH"
    assert make_settings(DEFAULT_MARKET="en-US").default_market.code == "en-US"


def test_a_requested_market_is_honoured():
    assert make_settings().resolve_market("de-CH").code == "de-CH"


def test_an_unknown_or_missing_market_falls_back_instead_of_failing():
    """An old bookmark or a stale client must not be able to fail a run."""
    settings = make_settings()
    assert settings.resolve_market("zz-ZZ").code == "de-CH"
    assert settings.resolve_market("").code == "de-CH"
    assert settings.resolve_market(None).code == "de-CH"


def test_a_market_that_is_not_enabled_is_refused_and_falls_back():
    """Disabling a market in config actually disables it, rather than leaving
    it reachable by anyone who knows the code."""
    settings = make_settings(ENABLED_MARKETS="en-US")
    assert settings.resolve_market("de-CH").code == "en-US"


def test_a_typo_in_the_enabled_list_does_not_take_the_service_down():
    settings = make_settings(ENABLED_MARKETS="en-US,de-CHH,de-CH")
    assert [m.code for m in settings.enabled_markets] == ["en-US", "de-CH"]


def test_the_picker_is_never_empty():
    settings = make_settings(ENABLED_MARKETS="nonsense,also-nonsense")
    assert len(settings.enabled_markets) >= 1


def test_a_default_outside_the_enabled_list_still_resolves():
    settings = make_settings(ENABLED_MARKETS="de-CH", DEFAULT_MARKET="en-US")
    assert settings.resolve_market(None).code == "de-CH"


def test_expected_queries_stays_37_across_markets():
    assert make_settings().expected_queries == 37
    assert make_settings(ENABLED_MARKETS="de-CH").expected_queries == 37


# ------------------------------------------------------------------- cache


def test_the_same_topic_in_two_markets_is_two_cache_entries():
    """Without this a German run could be served an English cached answer."""
    assert TTLCache.key("chalet zermatt", "en-US") != TTLCache.key("chalet zermatt", "de-CH")


def test_cache_key_still_normalises_the_topic():
    assert TTLCache.key("  Chalet   Zermatt ", "de-CH") == TTLCache.key(
        "chalet zermatt", "DE-CH"
    )


# -------------------------------------------------------------------- API


def test_status_advertises_both_markets(client, auth):
    test_client, _provider, _analyzer = client
    body = test_client.get("/api/status", headers=auth).json()

    codes = [m["code"] for m in body["markets"]]
    assert codes == ["en-US", "de-CH"]
    assert body["default_market"] == "de-CH"

    german = next(m for m in body["markets"] if m["code"] == "de-CH")
    assert german["question_modifiers"] == ["wer", "wie", "was", "wo", "warum", "welche"]
    assert german["expected_queries"] == 37


def test_analyze_passes_the_requested_market_to_collection(client, auth):
    test_client, provider, analyzer = client
    response = test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "de-CH"}, headers=auth
    )

    assert response.status_code == 200
    assert provider.last_market.code == "de-CH"
    # The model is told what language it is reading, so it does not mistake
    # German compounds for noise or try to translate them.
    assert analyzer.last_market.code == "de-CH"


def test_analyze_reports_the_market_it_used(client, auth):
    test_client, _provider, _analyzer = client
    body = test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "de-CH"}, headers=auth
    ).json()

    assert body["market"] == "de-CH"
    assert body["market_label"] == "Deutsch · Schweiz"


def test_omitting_the_market_uses_the_default(client, auth):
    test_client, provider, _analyzer = client
    body = test_client.post("/api/analyze", json={"topic": "villa"}, headers=auth).json()

    assert provider.last_market.code == "de-CH"
    assert body["market"] == "de-CH"


def test_an_unknown_market_falls_back_rather_than_400ing(client, auth):
    test_client, _provider, _analyzer = client
    response = test_client.post(
        "/api/analyze", json={"topic": "villa", "market": "zz-ZZ"}, headers=auth
    )

    assert response.status_code == 200
    assert response.json()["market"] == "de-CH"


def test_two_markets_do_not_share_a_cached_result(client, auth):
    """The second call must be a real run, not the first one's answer."""
    test_client, provider, _analyzer = client

    first = test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "en-US"}, headers=auth
    ).json()
    second = test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "de-CH"}, headers=auth
    ).json()

    assert provider.calls == 2
    assert first["market"] == "en-US"
    assert second["market"] == "de-CH"
    assert second["cached"] is False


def test_the_same_market_twice_is_served_from_cache(client, auth):
    test_client, provider, _analyzer = client

    test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "de-CH"}, headers=auth
    )
    second = test_client.post(
        "/api/analyze", json={"topic": "chalet zermatt", "market": "de-CH"}, headers=auth
    ).json()

    assert provider.calls == 1
    assert second["cached"] is True
    assert second["market"] == "de-CH"
