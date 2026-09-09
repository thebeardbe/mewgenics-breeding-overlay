"""Settings tab for the overlay.

Set-up actions that used to live in the window header: save selection (the
three in-game slots side by side + "open another save"), theme (segmented
choice) and zoom. Play-time toggles (pin, click-through) and ✕ live in the
header; the update notice lives on the tab row.

Each section is a framed card with a plain QLabel heading above it. QLabel
headings scale with the user zoom (QGroupBox::title pseudo-elements do not),
and the card frame stays put. Every control is theme-aware: the window calls
``set_active_theme`` and this tab re-derives its styles from the active theme
colours. User-facing copy avoids em-dashes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.ui import theme as _theme
from mewgenics_overlay.ui.theme import wrap_tooltip as _wt


def _hex(c) -> str:
    return str(getattr(_theme, c, "#6a6761"))


class SettingsTab(QWidget):
    def __init__(self, actions: dict, titles: dict | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._actions = actions
        self._titles = dict(titles or {})
        self._slot_buttons: list[QPushButton] = []
        self._slot_paths: list = []
        self._headings: list[QLabel] = []
        self._frames: list[QWidget] = []
        self._zoom_minus = None
        self._zoom_plus = None
        self._current = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setSpacing(4)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # Save section
        root.addWidget(self._heading("Save slots"))
        with self._frame(root) as lay:
            slot_row = QHBoxLayout()
            for n in range(3):
                btn = QPushButton(f"Slot {n + 1}\nno save yet")
                btn.setToolTip(_wt("Load this campaign slot's save. Slots "
                                   "that don't exist yet are disabled."))
                btn.setMinimumHeight(58)
                btn.clicked.connect(
                    lambda _=False, idx=n: self._actions["load_slot"](idx))
                btn.setEnabled(False)
                self._slot_buttons.append(btn)
                slot_row.addWidget(btn, 1)
            lay.addLayout(slot_row)
            open_btn = QPushButton("📁 Open another save file…")
            open_btn.setMaximumWidth(300)
            open_btn.clicked.connect(lambda: self._actions["open_save"]())
            open_row = QHBoxLayout()
            open_row.addWidget(open_btn)
            open_row.addStretch(1)
            lay.addLayout(open_row)

        # Appearance section
        root.addWidget(self._heading("Appearance"))
        with self._frame(root) as lay:
            theme_row = QHBoxLayout()
            theme_row.addWidget(QLabel("Theme:"))
            self._theme_buttons: dict = {}
            for key in self._titles:
                b = QPushButton(self._titles[key])
                b.setCheckable(True)
                b.setMinimumWidth(150)
                b.clicked.connect(lambda _=False, k=key:
                                  actions["set_theme"](k))
                self._theme_buttons[key] = b
                theme_row.addWidget(b)
            theme_row.addStretch(1)
            lay.addLayout(theme_row)

            zoom_row = QHBoxLayout()
            zoom_row.addWidget(QLabel("Zoom:"))
            self._zoom_minus = QPushButton("−")
            self._zoom_minus.setFixedSize(42, 32)
            self._zoom_minus.clicked.connect(lambda: actions["zoom_out"]())
            self._zoom_label = QLabel("100%")
            self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._zoom_label.setMinimumWidth(70)
            self._zoom_plus = QPushButton("+")
            self._zoom_plus.setFixedSize(42, 32)
            self._zoom_plus.clicked.connect(lambda: actions["zoom_in"]())
            reset_btn = QPushButton("Reset")
            reset_btn.setMinimumWidth(90)
            reset_btn.clicked.connect(lambda: actions["zoom_reset"]())
            zoom_row.addWidget(self._zoom_minus)
            zoom_row.addWidget(self._zoom_label)
            zoom_row.addWidget(self._zoom_plus)
            zoom_row.addWidget(reset_btn)
            zoom_row.addStretch(1)
            lay.addLayout(zoom_row)

        # About & support section
        root.addWidget(self._heading("About & support"))
        with self._frame(root) as lay:
            row = QHBoxLayout()
            about_btn = QPushButton("ℹ️ About")
            about_btn.setMinimumWidth(180)
            about_btn.clicked.connect(lambda: actions["about"]())
            report_btn = QPushButton("🐞 Report a problem")
            report_btn.setMinimumWidth(220)
            report_btn.clicked.connect(lambda: actions["report"]())
            row.addWidget(about_btn)
            row.addWidget(report_btn)
            row.addStretch(1)
            lay.addLayout(row)

        root.addStretch(1)
        self._restyle()

    # ── structure helpers ─────────────────────────────────────────────────
    def _heading(self, title: str) -> QLabel:
        lbl = QLabel(title)
        self._headings.append(lbl)
        return lbl

    def _frame(self, root):
        """Context manager: a framed QWidget whose layout is returned."""
        box = QWidget()
        box.setObjectName("secbox")
        self._frames.append(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        root.addWidget(box)

        class _Ctx:
            def __enter__(_self):
                return lay

            def __exit__(_self, *a):
                return False

        return _Ctx()

    # ── theming ───────────────────────────────────────────────────────────
    def _restyle(self) -> None:
        grip, muted, good = (_hex("C_GRIP"), _hex("C_MUTED"), _hex("C_GOOD"))
        for lbl in self._headings:
            lbl.setStyleSheet(f"font-weight: 700; color: {_hex('C_TEXT')};")
        for box in self._frames:
            box.setStyleSheet(
                f"#secbox {{ border: 1px solid {grip}; border-radius: 8px; }}")
        card = (
            f"QPushButton {{ background: transparent; border: 1px solid "
            f"{grip}; border-radius: 8px; padding: 6px 10px; "
            "text-align: center; }\n"
            f"QPushButton:hover {{ border-color: {muted}; }}\n"
            f"QPushButton:disabled {{ color: {muted}; "
            f"border-color: transparent; }}")
        for btn in self._slot_buttons:
            btn.setStyleSheet(card)
        for key, b in self._theme_buttons.items():
            b.setStyleSheet(
                f"{card}\n"
                f"QPushButton:checked {{ color: {good}; "
                f"border-color: {good}; }}")
        for z in (self._zoom_minus, self._zoom_plus):
            if z is not None:
                z.setStyleSheet(
                    f"QPushButton {{ background: transparent; border: 1px "
                    f"solid {grip}; border-radius: 6px; font-size: 17px; "
                    f"padding: 0; }}\n"
                    f"QPushButton:hover {{ border-color: {muted}; }}")
        self._refresh_slot_highlights()

    # ── save slots ────────────────────────────────────────────────────────
    def set_slots(self, entries) -> None:
        self._slot_paths = [p for _, p in entries]
        for i, (label, path) in enumerate(entries):
            if i >= len(self._slot_buttons):
                break
            btn = self._slot_buttons[i]
            if path:
                base = str(path).split("/")[-1].split("\\")[-1]
                btn.setText(f"{label}\n🐈 {base}")
                btn.setEnabled(True)
            else:
                btn.setText(f"{label}\nno save yet")
                btn.setEnabled(False)
        self._refresh_slot_highlights()

    def set_current(self, path) -> None:
        self._current = path or ""
        self._refresh_slot_highlights()

    def _refresh_slot_highlights(self) -> None:
        good = _hex("C_GOOD")
        for i, btn in enumerate(self._slot_buttons):
            if i >= len(self._slot_paths):
                break
            is_current = bool(self._slot_paths[i]) and \
                str(self._slot_paths[i]) == self._current
            text = btn.text()
            head, _, sub = text.partition("\n")
            sub = sub.replace("  ●", "")
            if is_current:
                sub = sub.rstrip() + "  ●"
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; "
                    f"border: 1px solid {good}; color: {good}; "
                    f"border-radius: 8px; padding: 6px 10px; "
                    f"text-align: center; }}\n"
                    f"QPushButton:hover {{ border-color: {good}; }}")
            else:
                grip, muted = _hex("C_GRIP"), _hex("C_MUTED")
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; "
                    f"border: 1px solid {grip}; border-radius: 8px; "
                    f"padding: 6px 10px; text-align: center; }}\n"
                    f"QPushButton:hover {{ border-color: {muted}; }}\n"
                    f"QPushButton:disabled {{ color: {muted}; "
                    f"border-color: transparent; }}")
            btn.setText(f"{head}\n{sub}")

    # ── state updates from the window ─────────────────────────────────────
    def set_active_theme(self, key: str) -> None:
        for k, b in self._theme_buttons.items():
            b.setChecked(k == key)
        self._restyle()

    def set_zoom(self, pct: int) -> None:
        self._zoom_label.setText(f"{pct}%")
