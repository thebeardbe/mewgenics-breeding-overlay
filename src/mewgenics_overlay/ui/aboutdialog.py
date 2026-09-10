"""The About dialog and the bug-report / debug-copy helpers.

Extracted from ``PaletteWindow`` (god-file split, final step): the credits
dialog, the "Report a problem" browser hand-off and the "Copy debug info"
clipboard block.

Window-agnostic: the live settings dict drives the configured report URL and
the debug block, and the pure helpers (``report_url``, ``debug_text``,
``copy_debug_to_clipboard``) can be called without a window, so the wording
and the URL guard stay testable. The dialog is a rich-text ``QDialog`` (not a
``QMessageBox``) because ``QMessageBox`` has no ``setOpenExternalLinks``.
"""

from __future__ import annotations

import platform
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay import __version__
from mewgenics_overlay.ui.links import REPORT_URL

# Default target for the report button: the self-hosted Bugbox report form,
# used whenever settings carry no valid report_url override (see ui/config.py).
DEFAULT_REPORT_URL = REPORT_URL


def report_url(configured: Optional[str] = None) -> str:
    """Where the About-box report button points (see config.report_url)."""
    url = configured or DEFAULT_REPORT_URL
    # Only ever hand an http(s) URL to the OS browser - never a custom
    # scheme from a config file (file:, or registered protocol handlers).
    if isinstance(url, str) and url.lower().startswith(("http://",
                                                        "https://")):
        return url
    return DEFAULT_REPORT_URL


def open_report(configured: Optional[str] = None) -> str:
    """Open the configured report URL in the OS browser; return that URL."""
    import webbrowser
    url = report_url(configured)
    webbrowser.open(url)
    return url


def debug_text(settings: dict) -> str:
    """The compact version/environment block copied into a bug report."""
    try:
        qt_ver = __import__("PySide6").__version__
    except Exception:
        qt_ver = "?"
    try:
        py_ver = platform.python_version()
    except Exception:
        py_ver = "?"
    data = settings or {}
    save = data.get("save_path") or ""
    return "\n".join([
        f"Mewgenics Breeding Overlay v{__version__}",
        f"OS: {platform.system()} {platform.release()}",
        f"Python: {py_ver} · Qt: {qt_ver}",
        f"Theme: {data.get('theme')}",
        f"Save: {save or '(none loaded)'}",
    ])


def copy_debug_to_clipboard(settings: dict) -> str:
    """Put ``debug_text(settings)`` on the clipboard; return the copied text."""
    text = debug_text(settings)
    QApplication.clipboard().setText(text)
    return text


def about_html(version: str = __version__) -> str:
    """Credits block: who built it and whose research it stands on."""
    return (
        f"<h3>Mewgenics Breeding Overlay</h3>"
        f"<p>Version {version}</p>"
        f"<p>Built by <b>TheBeardBE</b>, with help from an LLM "
        f"through <b>pi.dev</b>.</p>"
        f"<p><b>Credits</b></p>"
        f"<ul>"
        f"<li>Save parser &amp; genetics engine: "
        f"<a href='https://github.com/frankieg33/MewgenicsBreedingManager'>"
        f"MewgenicsBreedingManager</a> (MIT, © 2026 frankieg33) - "
        f"vendored; provenance in <code>vendor/_VENDORED.md</code></li>"
        f"<li>1.1 breeding-model sync: "
        f"<a href='https://github.com/whyayala/MewgenicsBreedingManager'>"
        f"whyayala's maintained fork</a> (v5.9.5) - same-sex rule, "
        f"gender-role compat gate, neutral-sexuality fix</li>"
        f"<li>Save-format research: "
        f"<a href='https://github.com/pzx521521/mewgenics-save-editor'>"
        f"pzx521521/mewgenics-save-editor</a> and the community</li>"
        f"<li>Breeding formulas (datamined from the game): "
        f"<a href='https://mewgenicswiki.org/tools/breeding-calculator'>"
        f"Mewgenics breeding calculator</a> (mewgenicswiki.org) + "
        f"<a href='https://gist.github.com/SciresM/95a9dbba22937420e75d4da617af1397'>"
        f"SciresM's game-code analysis</a>, cross-checked against "
        f"<a href='https://mewgenics.wiki.gg/wiki/Breeding'>wiki.gg's "
        f"datamined tables</a> - pinned by tests/test_wiki_math.py</li>"
        f"<li>Game mechanics reference: "
        f"<a href='https://mewgenics.wiki.gg/wiki/Mewgenics'>"
        f"Mewgenics Wiki</a></li>"
        f"</ul>"
        f"<p>Licensed MIT. Saves are read-only - this tool never "
        f"modifies them.</p>"
        f"<p>Update check: on start the app asks GitHub for the newest "
        f"release and shows a download button if one exists - no data is "
        f"sent. Disable it in Settings.</p>"
        f"<p>Found a problem? Use <b>🐞 Report a problem</b> below - "
        f"no account needed.</p>"
    )


class AboutDialog(QDialog):
    """Credits dialog: who built it and whose research it stands on.

    ``on_status`` receives the status-line text shown after "Copy debug
    info"; the host passes its own status setter so this module stays
    window-agnostic.
    """

    def __init__(self, settings: dict,
                 on_status: Optional[Callable[[str], None]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings or {}
        self._on_status = on_status
        self.setWindowTitle("About")
        self.setModal(True)
        layout = QVBoxLayout(self)
        label = QLabel(about_html())
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        report = QPushButton("🐞 Report a problem")
        report.setToolTip("Open the bug-report form in your browser - "
                          "no account needed.")
        report.clicked.connect(self._open_report)
        copy_info = QPushButton("📋 Copy debug info")
        copy_info.setToolTip("Copies version + save + theme to the clipboard "
                             "so a bug report needs no file hunting. Paste it "
                             "into the form.")
        copy_info.clicked.connect(self._copy_debug)
        row.addWidget(report)
        row.addWidget(copy_info)
        row.addStretch()
        layout.addWidget(actions)
        layout.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        self.resize(560, 480)

    def _open_report(self) -> None:
        open_report(self._settings.get("report_url"))

    def _copy_debug(self) -> None:
        copy_debug_to_clipboard(self._settings)
        if self._on_status is not None:
            self._on_status("debug info copied - paste it into a bug report")


def show_about(parent, settings: dict,
               on_status: Optional[Callable[[str], None]] = None) -> None:
    """Build and run the About dialog modally for *parent*."""
    AboutDialog(settings, on_status, parent).exec()
