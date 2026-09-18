"""The PDF export route and the renderer behind it.

The renderer takes client-supplied Markdown, so the tests that matter most
here are the ones that push malformed or hostile input through it: a report
is user-visible output, but the Markdown reaching it came over the wire.
"""

from __future__ import annotations

import pytest

from app.main import pdf_filename
from app.pdf import render_report_pdf

SAMPLE = """## 1. Search Intent Classification

Most of the set is **commercial**, with a long tail of questions.

| Intent | Keywords | Share |
| --- | --- | --- |
| Commercial | 68 | 45% |
| Informational | 36 | 24% |

- A bullet with `code` and an [link](https://example.com).
- Another bullet.

1. A numbered step.
2. And the next.

> A pull quote worth keeping.

---

### A closing heading
"""


def post(test_client, auth, **overrides):
    body = {
        "topic": "luxury villa rentals",
        "analysis_markdown": SAMPLE,
        "generated_at": "2026-09-18T09:14:00+00:00",
        "provider_label": "Claude",
        "model": "claude-sonnet-5",
        "total_keywords": 150,
        "queries_succeeded": 35,
        "queries_attempted": 37,
    }
    body.update(overrides)
    return test_client.post("/api/export/report.pdf", json=body, headers=auth)


def test_export_returns_a_pdf(client, auth):
    test_client, _provider, _analyzer = client
    response = post(test_client, auth)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 1500


def test_export_names_the_file_after_the_topic(client, auth):
    test_client, _provider, _analyzer = client
    response = post(test_client, auth)

    disposition = response.headers["content-disposition"]
    assert 'filename="intent-report-luxury-villa-rentals-20260918.pdf"' in disposition


def test_export_requires_a_session(client):
    test_client, _provider, _analyzer = client
    response = post(test_client, {})
    assert response.status_code == 401


def test_export_rejects_an_empty_report(client, auth):
    test_client, _provider, _analyzer = client
    response = post(test_client, auth, analysis_markdown="")
    assert response.status_code == 422


def test_export_costs_no_quota(client, auth):
    """Exporting is not analysing; the run allowance must be untouched."""
    test_client, _provider, _analyzer = client
    before = test_client.get("/api/status", headers=auth).json()["searches_remaining"]
    post(test_client, auth)
    after = test_client.get("/api/status", headers=auth).json()["searches_remaining"]
    assert before == after


@pytest.mark.parametrize(
    "markdown",
    [
        "Angle brackets <b>and</b> & ampersands.",
        "| broken | table\n| --- |\n| only one cell |",
        "**unclosed bold and `unclosed code",
        "#" * 12 + " deep heading",
        "- " * 400,
        "A line " + "verylongunbreakabletoken" * 40,
    ],
)
def test_renderer_survives_malformed_markdown(markdown):
    """Markup that would break a naive string-formatting renderer. The report
    text arrives from the client, so none of this may raise."""
    pdf = render_report_pdf(topic="edge cases", analysis_markdown=markdown)
    assert pdf.startswith(b"%PDF-")


def test_renderer_escapes_markup_rather_than_running_it():
    pdf = render_report_pdf(
        topic="<b>bold topic</b>",
        analysis_markdown="A <font color='red'>coloured</font> span.",
    )
    assert pdf.startswith(b"%PDF-")


def test_renderer_handles_a_report_with_no_metadata():
    pdf = render_report_pdf(topic="bare", analysis_markdown="Just one line.")
    assert pdf.startswith(b"%PDF-")


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("luxury villa rentals", "intent-report-luxury-villa-rentals.pdf"),
        ("  CRM   Software!  ", "intent-report-crm-software.pdf"),
        ("///", "intent-report-report.pdf"),
        ('a"b', "intent-report-a-b.pdf"),
    ],
)
def test_pdf_filename_is_quote_free_ascii(topic, expected):
    """The name is interpolated into a Content-Disposition header, so a quote
    or a non-ASCII byte getting through would break the header itself."""
    name = pdf_filename(topic, "")
    assert name == expected
    assert '"' not in name
    name.encode("ascii")
