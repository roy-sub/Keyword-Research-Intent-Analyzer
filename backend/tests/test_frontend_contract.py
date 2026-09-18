"""Guards the contract between this API and the frontend in the sibling folder.

The frontend renders `mock-response.json` through the same code path as a
real response, so if that fixture and this service's response model drift
apart, mock mode silently stops representing reality. This test fails loudly
instead.

It is skipped when the frontend folder is absent, so the backend remains
runnable and testable on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import AnalyzeResponse, KeywordEntry, SourceGroup

MOCK_RESPONSE = Path(__file__).resolve().parents[2] / "frontend" / "mock-response.json"

pytestmark = pytest.mark.skipif(
    not MOCK_RESPONSE.is_file(), reason="frontend/ not present next to backend/"
)


@pytest.fixture(scope="module")
def mock_payload() -> dict:
    return json.loads(MOCK_RESPONSE.read_text(encoding="utf-8"))


def test_mock_response_validates_against_the_response_model(mock_payload):
    AnalyzeResponse.model_validate(mock_payload)


def test_mock_response_has_exactly_the_response_fields(mock_payload):
    assert set(mock_payload) == set(AnalyzeResponse.model_fields)


def test_nested_shapes_match(mock_payload):
    assert set(mock_payload["sources"][0]) == set(SourceGroup.model_fields)
    assert set(mock_payload["keywords"][0]) == set(KeywordEntry.model_fields)


def test_frontend_calls_only_routes_this_service_exposes():
    """Every apiUrl("...") in app.js must correspond to a real route."""
    app_js = MOCK_RESPONSE.parent / "app.js"
    import re

    from app.config import get_settings
    from app.main import create_app

    called = set(re.findall(r'apiUrl\("([^"]+)"\)', app_js.read_text(encoding="utf-8")))
    assert called, "no apiUrl() call sites found — did app.js change shape?"

    exposed = {route.path for route in create_app(get_settings()).routes}
    assert called <= exposed, f"frontend calls unknown routes: {sorted(called - exposed)}"
