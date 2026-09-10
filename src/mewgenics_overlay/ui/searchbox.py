"""SearchBox - the cat search line plus its results dropdown.

Extracted from ``PaletteWindow`` (god-file split, step 1): owns the
QLineEdit, the results QListWidget and the focus/clear state machine that
decides when the dropdown opens, when a clear resets the current cat and
when choosing a result re-focuses the window.

It has no knowledge of the rest of the overlay: the live session, the
"a cat was chosen" action and the "the user cleared the box" action arrive
as plain callables.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.core.session import Session, display_location
from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt

_RESULTS_MAX_HEIGHT = 170   # dropdown cap so it never swallows the window
_SEARCH_LIMIT = 100         # candidates scanned for a typed query
_RESULTS_SHOWN = 60         # rows rendered before the "more matches" hint
_ARROW_SIZE = 12            # 12x12 down-arrow icon in the line edit
_DEFAULT_SPACING = 6        # fallback when the host passes no spacing


class SearchBox(QWidget):
    """The search line and its dropdown, wired to plain callbacks.

    ``session_getter`` returns the current ``Session`` (or ``None``);
    ``on_choose`` receives the picked ``db_key``; ``on_clear`` is called when
    a manual clear should reset whatever the host window is showing.
    ``spacing`` is the gap used by this widget's own layout; the host passes
    its own layout spacing so the search row lines up with the rest.
    """

    def __init__(
        self,
        session_getter: Callable[[], Optional[Session]],
        on_choose: Callable[[int], None],
        on_clear: Callable[[], None],
        parent: Optional[QWidget] = None,
        spacing: int = _DEFAULT_SPACING,
    ) -> None:
        super().__init__(parent)
        self._session_getter = session_getter
        self._on_choose = on_choose
        self._on_clear = on_clear
        self._suppress_clear_text = False
        self._opened_by_focus = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            "Click a cat in-game, then type its name here…")
        self.edit.setClearButtonEnabled(True)
        # down-arrow affordance: opens the full-cat dropdown
        self._dropdown_action = self.edit.addAction(
            self._arrow_icon(), QLineEdit.ActionPosition.TrailingPosition)
        self._dropdown_action.setToolTip("Show all cats")
        self._dropdown_action.triggered.connect(self._open_search_dropdown)
        self.edit.setToolTip(_wt(
            "Find a cat by typing part of its name - the list comes from "
            "your latest save and refreshes on its own whenever the game "
            "saves.\n"
            "Then pick who to analyse for breeding."
        ))
        self.list = QListWidget()
        self.list.setVisible(False)
        self.list.setMaximumHeight(_RESULTS_MAX_HEIGHT)
        lay.addWidget(self.edit)
        lay.addWidget(self.list)

        self.edit.textChanged.connect(self._on_search_text)
        self.edit.installEventFilter(self)
        self.edit.returnPressed.connect(self._on_search_enter)
        self.list.itemClicked.connect(self._on_result_clicked)
        self.list.itemActivated.connect(self._on_result_clicked)

    # ── public state helpers ───────────────────────────────────────────────
    def set_text_silently(self, text: str) -> None:
        """Set the line text without running the manual-clear path."""
        self._suppress_clear_text = True
        self.edit.setText(text)
        self._suppress_clear_text = False

    def clear_focus(self) -> None:
        """Drop keyboard focus from the search line."""
        self.edit.clearFocus()

    # ── dropdown ───────────────────────────────────────────────────────────
    def _arrow_icon(self) -> QIcon:
        """Tiny down-arrow QIcon in the current theme's muted colour."""
        pm = QPixmap(_ARROW_SIZE, _ARROW_SIZE)
        pm.fill(QColor(0, 0, 0, 0))
        pnt = QPainter(pm)
        pnt.setRenderHint(QPainter.RenderHint.Antialiasing)
        pnt.setPen(QPen(QColor(_theme.C_MUTED), 2))
        pnt.drawLine(1, 4, 6, 9)
        pnt.drawLine(6, 9, 11, 4)
        pnt.end()
        return QIcon(pm)

    def _open_search_dropdown(self) -> None:
        """Arrow click: open the dropdown, or close it if already open.
        Never clears the current selection/table. A click that merely focuses
        the box (which itself opens the list) must not close it again."""
        if self._opened_by_focus:
            self._opened_by_focus = False
            if self._session_getter() is not None and not self.list.isVisible():
                self._fill_dropdown(self.edit.text())
            return
        if self.list.isVisible():
            self.list.setVisible(False)
            return
        if self._session_getter() is None:
            return
        self._fill_dropdown(self.edit.text())

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API)
        """Show the full cat dropdown when the user actively focuses the
        search box (click, Tab or keyboard shortcut). Pure window activation
        on Wayland/Hyprland does NOT open it.

        If another widget ever needs filtering, dispatch on ``watched`` in a
        separate method - do not stack more ``if watched is ...`` branches
        here."""
        if watched is self.edit and event.type() == QEvent.Type.FocusIn:
            reason = event.reason()
            explicit = reason in (
                Qt.FocusReason.MouseFocusReason,
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            )
            if explicit and self._session_getter() is not None:
                self._opened_by_focus = True
                self._fill_dropdown(self.edit.text())
        return super().eventFilter(watched, event)

    def _fill_dropdown(self, text: str) -> None:
        """Fill the results list and set its visibility from the result:
        hidden when there is nothing to show (empty roster or no hits), so
        callers must not re-show it unconditionally. No side effects (does
        not clear the current cat/table). Opening via the arrow or focusing
        the box must never behave like the user pressing the clear X."""
        session = self._session_getter()
        self.list.clear()
        if session is None:
            self.list.setVisible(False)
            return
        text = text.strip()
        if text:
            hits = session.search(text, limit=_SEARCH_LIMIT)
            truncated = len(hits) > _RESULTS_SHOWN
            if truncated:
                hits = hits[:_RESULTS_SHOWN]
        else:
            hits = sorted(session.alive, key=lambda c: c.name.lower())
            truncated = len(hits) > _RESULTS_SHOWN
        for c in hits[:_RESULTS_SHOWN]:
            item = QListWidgetItem(
                f"{c.name}   · {display_location(c)}   · {c.gender}   · "
                f"sum {sum(c.base_stats.values())}"
            )
            item.setData(Qt.ItemDataRole.UserRole, c.db_key)
            self.list.addItem(item)
        if truncated:
            more = QListWidgetItem("… more matches - type more of the name")
            more.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(more)
        self.list.setVisible(bool(hits) or truncated)

    # ── focus / clear state machine ────────────────────────────────────────
    def _on_search_text(self, text: str) -> None:
        """Typing/clearing. A manual clear (built-in X / backspace on the
        focused box) also resets the current cat and its table."""
        if not text.strip() and not self._suppress_clear_text \
                and self.edit.hasFocus():
            self._on_clear()
        self._fill_dropdown(text)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        self._on_choose(item.data(Qt.ItemDataRole.UserRole))
        self._opened_by_focus = False
        self.list.setVisible(False)

    def _on_search_enter(self) -> None:
        if self.list.count():
            self._on_result_clicked(self.list.item(0))
