import html

import pytest

from app.roadmap import NAV_PHASES


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "No accounts yet" in r.text
    assert "Roadmap" in r.text


@pytest.mark.parametrize("phase", [p for p in NAV_PHASES if p.status != "done"], ids=lambda p: p.slug)
def test_unbuilt_sections_show_placeholder(client, phase):
    r = client.get(f"/{phase.slug}")
    assert r.status_code == 200
    assert f"Built in Phase {phase.number}" in r.text


def test_unknown_page_is_404_html(client):
    r = client.get("/nope", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert "doesn't exist" in html.unescape(r.text)


def test_phases_without_pages_are_404(client):
    assert client.get("/foundation").status_code == 404
    assert client.get("/release").status_code == 404


def test_static_css_served(client):
    assert client.get("/static/css/app.css").status_code == 200
