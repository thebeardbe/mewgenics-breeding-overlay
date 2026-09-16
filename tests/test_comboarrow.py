"""Combo arrow QSS URL: quoted and forward-slashed so Windows paths work.

``ui/comboarrow.py`` renders a down-arrow PNG per theme colour and attaches
it with a widget-local stylesheet rule. Qt's stylesheet parser cannot express
a bare Windows path - backslashes plus the spaces in a user profile directory
make the rule fail to parse and the combo draws no arrow. ``qss_url``
normalises the separators to ``/`` and quotes the value; a quoted POSIX URL
stays valid.

``PureWindowsPath`` supplies a genuine Windows-style path even where the
tests run on Linux, and vice versa. No save, window or rendered PNG is
needed: the URL helper is pure, and ``style_combo`` is exercised with the
file lookup stubbed.
"""

from __future__ import annotations

import os

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path, PurePosixPath, PureWindowsPath  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from mewgenics_overlay.ui import comboarrow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ── qss_url ────────────────────────────────────────────────────────────────
def test_qss_url_quotes_and_forward_slashes_a_windows_path():
    win = PureWindowsPath(r"C:\Users\a b\AppData\Local\overlay\combo.png")

    url = comboarrow.qss_url(win)

    assert url == 'url("C:/Users/a b/AppData/Local/overlay/combo.png")'
    assert "\\" not in url
    assert url.startswith('url("') and url.endswith('")')


def test_qss_url_leaves_a_posix_path_usable():
    posix = PurePosixPath("/home/mîlø/.config/mewgenics/combo arrow.png")

    url = comboarrow.qss_url(posix)

    assert url == 'url("/home/mîlø/.config/mewgenics/combo arrow.png")'
    assert "\\" not in url
    assert url.startswith('url("') and url.endswith('")')


def test_qss_url_is_not_the_bare_unquoted_form():
    # Regression: the old ``url({path})`` form broke the rule on Windows
    # (backslashes, spaces). A real ``Path`` must come out quoted.
    for path in (PureWindowsPath(r"C:\x\arrow.png"), Path("/tmp/arrow.png")):
        url = comboarrow.qss_url(path)

        assert url == 'url("{}")'.format(path.as_posix())
        assert url != "url({})".format(path.as_posix())
        assert url.startswith('url("')


# ── style_combo embeds the helper's output ─────────────────────────────────
def test_style_combo_embeds_the_quoted_windows_url(qapp, monkeypatch):
    win = PureWindowsPath(r"C:\Users\a b\combo.png")
    monkeypatch.setattr(comboarrow, "arrow_file", lambda color: win)
    combo = QComboBox()

    comboarrow.style_combo(combo, "#abcdef")

    qss = combo.styleSheet()
    assert 'image: url("C:/Users/a b/combo.png")' in qss
    assert "image: url(C:" not in qss          # no bare Windows path
    assert "QComboBox::down-arrow" in qss
    assert "QComboBox::drop-down" in qss
    combo.deleteLater()
    qapp.processEvents()


def test_style_combo_keeps_a_posix_url_quoted(qapp, monkeypatch):
    posix = Path("/tmp/overlay/arrow.png")
    monkeypatch.setattr(comboarrow, "arrow_file", lambda color: posix)
    combo = QComboBox()

    comboarrow.style_combo(combo, "#123456")

    assert 'image: url("/tmp/overlay/arrow.png")' in combo.styleSheet()
    combo.deleteLater()
    qapp.processEvents()
