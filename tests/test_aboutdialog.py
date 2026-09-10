"""About dialog, bug-report URL guard and debug-copy block.

``mewgenics_overlay.ui.aboutdialog`` (final god-file split step) holds the
credits dialog plus three pure helpers. The pure helpers need no window; the
dialog itself is built offscreen. The browser hand-off is always patched so no
test can ever open a real browser or touch the network.
"""

from __future__ import annotations

import os
import platform
import webbrowser

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from mewgenics_overlay import __version__  # noqa: E402
from mewgenics_overlay.ui import aboutdialog as ad  # noqa: E402
from mewgenics_overlay.ui import links as _links  # noqa: E402
from mewgenics_overlay.ui.aboutdialog import (  # noqa: E402
    DEFAULT_REPORT_URL,
    AboutDialog,
    about_html,
    debug_text,
    open_report,
    report_url,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def opener(monkeypatch):
    """Record webbrowser.open calls instead of launching anything."""
    calls = []

    def fake_open(url, *args, **kwargs):
        calls.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", fake_open)
    return calls


# ── 1. report_url ──────────────────────────────────────────────────────────
def test_report_url_defaults_to_the_website_report_form():
    # The default comes from ui/links.py, the single home for site URLs.
    assert DEFAULT_REPORT_URL == _links.REPORT_URL
    assert DEFAULT_REPORT_URL == _links.SITE_URL + "report"
    assert "github.com" not in DEFAULT_REPORT_URL
    assert report_url() == DEFAULT_REPORT_URL
    assert report_url(None) == DEFAULT_REPORT_URL
    assert report_url("") == DEFAULT_REPORT_URL


def test_report_url_keeps_a_custom_https_value():
    custom = "https://bugbox.example/report?app=overlay"
    assert report_url(custom) == custom


def test_report_url_accepts_a_plain_http_value():
    custom = "http://localhost:8080/report"
    assert report_url(custom) == custom


@pytest.mark.parametrize("configured", [
    "file:///etc/passwd",              # local files
    "javascript:alert(1)",             # script scheme
    "  https://padded.example",        # unconsumed whitespace
    "ftp://host/report",
    42,
    ["https://list.example"],
])
def test_report_url_falls_back_for_anything_but_a_bare_http_scheme(
        configured):
    # The guard is deliberately strict: only a string that itself starts with
    # http(s) ever reaches the OS browser.
    assert report_url(configured) == DEFAULT_REPORT_URL


def test_report_url_does_not_read_the_environment(monkeypatch):
    """Documented gap: there is no MEWGENICS_REPORT_URL support.

    A custom report target arrives as the ``configured`` argument (the live
    settings dict's ``report_url``), never from the environment.
    """
    monkeypatch.setenv("MEWGENICS_REPORT_URL", "https://env.example/report")

    assert report_url() == DEFAULT_REPORT_URL


# ── 2. open_report ─────────────────────────────────────────────────────────
def test_open_report_uses_the_url_opener_and_returns_the_url(opener):
    url = "https://bugbox.example/report"

    result = open_report(url)

    assert result == url
    assert opener == [url]


def test_open_report_falls_back_to_the_default_url(opener):
    result = open_report("file:///etc/passwd")

    assert result == DEFAULT_REPORT_URL
    assert opener == [DEFAULT_REPORT_URL]


def test_open_report_without_a_configured_url_opens_the_default(opener):
    assert open_report() == DEFAULT_REPORT_URL
    assert opener == [DEFAULT_REPORT_URL]


# ── 3. debug_text ──────────────────────────────────────────────────────────
def test_debug_text_reports_version_save_theme_os_and_runtimes():
    text = debug_text({"save_path": "/saves/steamcampaign01.sav",
                       "theme": "film"})

    assert f"Mewgenics Breeding Overlay v{__version__}" in text
    assert f"OS: {platform.system()} {platform.release()}" in text
    assert "Python:" in text and "Qt:" in text
    assert "Theme: film" in text
    assert "Save: /saves/steamcampaign01.sav" in text


def test_debug_text_with_no_settings_says_none_loaded():
    text = debug_text({})

    assert "Theme: None" in text
    assert "Save: (none loaded)" in text


def test_debug_text_never_leaks_tokens_or_the_report_url():
    secret = "https://bugbox.example/report?token=SUPERSECRET"
    text = debug_text({"save_path": "/s/x.sav", "theme": "noir",
                       "report_url": secret, "token": "SUPERSECRET",
                       "api_key": "SUPERSECRET"})

    assert "SUPERSECRET" not in text
    assert secret not in text
    assert "report_url" not in text


def test_debug_text_handles_a_none_settings_argument():
    text = debug_text(None)

    assert f"v{__version__}" in text
    assert "Save: (none loaded)" in text


# ── 4. about_html credits ──────────────────────────────────────────────────
@pytest.mark.parametrize("credit", [
    "MewgenicsBreedingManager",          # the vendored upstream (MBM)
    "frankieg33",
    "whyayala",                          # maintained fork
    "pzx521521",                         # save-format research (pz)
    "TheBeardBE",
    "pi.dev",
    "SciresM",
    "mewgenicswiki.org",
])
def test_about_html_lists_every_credit(credit):
    assert credit in about_html()


def test_about_html_states_the_read_only_promise():
    html = about_html()

    assert "read-only" in html
    assert "never" in html and "modifies them" in html


def test_about_html_shows_the_given_version():
    assert "Version 9.9.9" in about_html("9.9.9")
    assert f"Version {__version__}" in about_html()


def test_about_html_external_links_are_https_or_http():
    import re

    html = about_html()
    hrefs = re.findall(r"href='([^']+)'", html)

    assert hrefs, "the credits block should link out"
    assert all(h.startswith(("https://", "http://")) for h in hrefs)


# ── 5. AboutDialog ─────────────────────────────────────────────────────────
@pytest.fixture
def make_dialog(qapp):
    dialogs = []

    def _make(payload=None, on_status=None):
        dlg = AboutDialog(payload if payload is not None else {}, on_status)
        dialogs.append(dlg)
        return dlg

    yield _make

    for dlg in dialogs:
        dlg.hide()
        dlg.close()
        dlg.deleteLater()
    qapp.processEvents()


def _button(dlg, needle):
    for btn in dlg.findChildren(QPushButton):
        if needle in btn.text():
            return btn
    raise AssertionError(f"no button containing {needle!r}")


def test_dialog_is_modal_and_offers_report_copy_and_ok(make_dialog):
    dlg = make_dialog()

    assert dlg.windowTitle() == "About"
    assert dlg.isModal() is True
    assert "🐞 Report a problem" in _button(dlg, "Report a problem").text()
    assert "📋 Copy debug info" in _button(dlg, "Copy debug info").text()
    assert _button(dlg, "OK").text() == "OK"


def test_dialog_report_button_uses_the_settings_url(make_dialog, monkeypatch):
    seen = []
    monkeypatch.setattr(ad, "open_report", lambda url=None: seen.append(url))
    dlg = make_dialog({"report_url": "https://bugbox.example/report"})

    _button(dlg, "Report a problem").click()

    assert seen == ["https://bugbox.example/report"]


def test_dialog_report_button_without_a_settings_url_passes_none(
        make_dialog, monkeypatch):
    seen = []
    monkeypatch.setattr(ad, "open_report", lambda url=None: seen.append(url))
    dlg = make_dialog({})

    _button(dlg, "Report a problem").click()

    assert seen == [None]


def test_copy_debug_button_fills_the_clipboard_and_reports_status(make_dialog):
    status = []
    dlg = make_dialog({"save_path": "/saves/x.sav", "theme": "noir"},
                      on_status=status.append)

    _button(dlg, "Copy debug info").click()

    clipboard = QApplication.clipboard().text()
    assert clipboard == debug_text({"save_path": "/saves/x.sav",
                                    "theme": "noir"})
    assert "v" in clipboard and "/saves/x.sav" in clipboard
    assert status == ["debug info copied - paste it into a bug report"]


def test_copy_debug_without_a_status_callback_does_not_raise(make_dialog):
    dlg = make_dialog({"theme": "film"})

    _button(dlg, "Copy debug info").click()      # must not raise

    assert "Theme: film" in QApplication.clipboard().text()


def test_dialog_ok_button_accepts(make_dialog):
    dlg = make_dialog()
    dlg.show()

    _button(dlg, "OK").click()

    assert dlg.isVisible() is False
