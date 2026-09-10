"""Pure tests for the update checker and the site-link single source (no network)."""

from mewgenics_overlay.ui import links as _links
from mewgenics_overlay.ui import update_check as _updates
from mewgenics_overlay.ui.update_check import (
    CHECK_INTERVAL,
    RELEASES_URL,
    UPDATE_API_URL,
    due,
    is_newer,
    latest_release,
    parse_version,
)


# ── 1. user-facing links live in ui/links.py ──────────────────────────────
def test_site_url_is_the_website_home():
    assert _links.SITE_URL == "https://mewgenics.thebeard.be/"


def test_download_url_anchors_the_site_download_section():
    assert _links.DOWNLOAD_URL == _links.SITE_URL + "#download"
    assert _links.DOWNLOAD_URL.startswith(_links.SITE_URL)
    assert _links.DOWNLOAD_URL.endswith("#download")


def test_report_url_anchors_the_site_report_form():
    assert _links.REPORT_URL == _links.SITE_URL + "report"
    assert _links.REPORT_URL.startswith(_links.SITE_URL)
    assert _links.REPORT_URL.endswith("report")


def test_download_and_report_urls_derive_from_one_shared_site_url():
    # Both user-facing links are built from the same SITE_URL, so a domain
    # change moves them together and cannot leave one on the old host.
    assert _links.DOWNLOAD_URL[len(_links.SITE_URL):] == "#download"
    assert _links.REPORT_URL[len(_links.SITE_URL):] == "report"


def test_site_links_are_not_the_github_release_page():
    for url in (_links.SITE_URL, _links.DOWNLOAD_URL, _links.REPORT_URL):
        assert "github.com" not in url
    assert "/releases" not in _links.DOWNLOAD_URL


def test_update_check_does_not_duplicate_the_site_links():
    # Single home for the website URLs: ui/links.py, not update_check.py.
    for name in ("SITE_URL", "DOWNLOAD_URL", "REPORT_URL"):
        assert not hasattr(_updates, name), f"{name} belongs in ui/links.py"


def test_version_discovery_still_uses_the_github_api():
    # The links moved to the website; the latest-release lookup did not.
    assert UPDATE_API_URL.startswith("https://api.github.com/")
    assert "mewgenics-breeding-overlay" in UPDATE_API_URL
    assert UPDATE_API_URL.endswith("/releases/latest")
    assert RELEASES_URL.startswith("https://github.com/")
    assert RELEASES_URL.endswith("/releases/latest")


def test_parse_version_variants():
    assert parse_version("v0.1.47") == (0, 1, 47)
    assert parse_version("0.1.47") == (0, 1, 47)
    assert parse_version("1.2.3-beta") == (1, 2, 3)
    assert parse_version("0.2.0") == (0, 2, 0)
    assert parse_version("") == (0,)
    assert parse_version(None) == (0,)


def test_is_newer():
    assert is_newer("0.1.47", "0.1.48") is True
    assert is_newer("0.1.47", "0.2.0") is True
    assert is_newer("0.1.47", "0.1.47") is False
    assert is_newer("0.1.48", "0.1.47") is False
    assert is_newer("0.1.47", "0.1.47-beta") is False


def test_due_respects_interval():
    now = 1000000.0
    assert due(None, now) is True
    assert due(now - CHECK_INTERVAL, now) is True
    assert due(now - CHECK_INTERVAL + 10, now) is False


# ── 2. latest_release: a bare version only, no URL pair ───────────────────
class _FakeResponse:
    """Minimal urlopen() stand-in: only geturl() is used by the fallback."""

    def __init__(self, final_url):
        self._final_url = final_url

    def geturl(self):
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_latest_release_returns_a_bare_version_tuple(monkeypatch):
    monkeypatch.setattr(_updates, "_api_latest", lambda timeout: (0, 2, 0))

    result = latest_release()

    # A (version, url) pair would be a 2-tuple: the returned value must be the
    # version tuple itself so the notice can compare and label it directly.
    assert result == (0, 2, 0)
    assert isinstance(result, tuple) and len(result) == 3


def test_latest_release_rate_limit_fallback_parses_the_tag(monkeypatch):
    monkeypatch.setattr(_updates, "_api_latest", lambda timeout: None)
    monkeypatch.setattr(
        _updates.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(
            "https://github.com/thebeardbe/mewgenics-breeding-overlay"
            "/releases/tag/v0.3.1"))

    assert latest_release() == (0, 3, 1)


def test_latest_release_returns_none_when_every_source_fails(monkeypatch):
    monkeypatch.setattr(_updates, "_api_latest", lambda timeout: None)

    def offline(req, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(_updates.urllib.request, "urlopen", offline)

    assert latest_release() is None
