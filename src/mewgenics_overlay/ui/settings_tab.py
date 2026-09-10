"""Settings tab for the overlay.

Set-up actions that used to live in the window header: save selection (the
three in-game slots side by side, owned by ``ui/savepanel.py`` and passed in
by the window), theme (segmented choice) and zoom. Play-time toggles (pin,
click-through) and ✕ live in the header; the update notice lives on the tab
row.

Each section is a framed card with a plain QLabel heading above it. QLabel
headings scale with the user zoom (QGroupBox::title pseudo-elements do not),
and the card frame stays put. Every control is theme-aware: the window calls
``set_active_theme`` and this tab re-derives its styles from the active theme
colours (the save slots restyle themselves). User-facing copy avoids
em-dashes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mewgenics_overlay.ui import hotkeybinding
from mewgenics_overlay.ui import theme as _theme


def _hex(c) -> str:
    return str(getattr(_theme, c, _theme.C_FALLBACK))


class SettingsTab(QWidget):
    def __init__(self, actions: dict, save_panel: QWidget,
                 titles: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._actions = actions
        self._save_panel = save_panel
        self._titles = dict(titles or {})
        self._headings: list[QLabel] = []
        self._frames: list[QWidget] = []
        self._zoom_minus = None
        self._zoom_plus = None
        self._current = ""
        self._hotkey_hint = None
        self._hotkey_hint_text = f"Global hotkey: {hotkeybinding.DEFAULT_TEXT}"
        self._hotkey_hint_error = False

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

        # Save section: the slot cards + picker are their own widget
        # (ui/savepanel.py) so the same controls serve the tray menu.
        root.addWidget(self._heading("Save slots"))
        with self._frame(root) as lay:
            lay.addWidget(self._save_panel)

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

            self._update_check = QCheckBox("Check for updates on start")
            self._update_check.setToolTip(
                "Ask GitHub for a newer release when the app starts and "
                "show a download button if one exists. No data is sent.")
            self._update_check.toggled.connect(
                lambda on: actions["set_check_updates"](on))
            lay.addWidget(self._update_check)

        # Global hotkey section: the combo is applied through the injected
        # set_hotkey action, which owns registration and persistence.
        root.addWidget(self._heading("Global hotkey"))
        with self._frame(root) as lay:
            hk_row = QHBoxLayout()
            self._hotkey_mods = {}
            for name in ("ctrl", "alt", "shift"):
                cb = QCheckBox(name.capitalize())
                cb.toggled.connect(self._apply_hotkey)
                self._hotkey_mods[name] = cb
                hk_row.addWidget(cb)
            hk_row.addWidget(QLabel("Key:"))
            self._hotkey_key = QLineEdit()
            self._hotkey_key.setMaxLength(1)
            self._hotkey_key.setFixedWidth(48)
            self._hotkey_key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._hotkey_key.setToolTip(
                "One letter A to Z (stored uppercase).\n"
                "The combination also needs at least one modifier.")
            self._hotkey_key.textChanged.connect(self._on_hotkey_letter)
            hk_row.addWidget(self._hotkey_key)
            hk_row.addStretch(1)
            lay.addLayout(hk_row)
            self._hotkey_hint = QLabel(self._hotkey_hint_text)
            self._hotkey_hint.setObjectName("muted")
            self._hotkey_hint.setWordWrap(True)
            lay.addWidget(self._hotkey_hint)

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
        self._restyle_hotkey_hint()
        self._save_panel.restyle()

    def _restyle_hotkey_hint(self) -> None:
        """Colour the hotkey hint: risk colour for an inline error, muted
        otherwise. Re-run by ``_restyle`` on every theme switch."""
        if self._hotkey_hint is None:
            return
        colour = _hex("RISK_HIGH") if self._hotkey_hint_error else _hex("C_MUTED")
        self._hotkey_hint.setText(self._hotkey_hint_text)
        self._hotkey_hint.setStyleSheet(f"color: {colour};\n")

    # ── state updates from the window ─────────────────────────────────────
    def set_active_theme(self, key: str) -> None:
        for k, b in self._theme_buttons.items():
            b.setChecked(k == key)
        self._restyle()

    def set_check_updates(self, on: bool) -> None:
        """Reflect the persisted setting in the checkbox (no signal).

        A state update from the window, like ``set_active_theme``: it must
        not re-fire ``toggled`` or the startup sync would write the unchanged
        value straight back to the settings file.
        """
        blocked = self._update_check.blockSignals(True)
        self._update_check.setChecked(bool(on))
        self._update_check.blockSignals(blocked)

    def set_zoom(self, pct: int) -> None:
        self._zoom_label.setText(f"{pct}%")

    # ── global hotkey ─────────────────────────────────────────────────────
    def _hotkey_text(self) -> str:
        parts = [name.capitalize() for name, cb in self._hotkey_mods.items()
                 if cb.isChecked()]
        parts.append(self._hotkey_key.text().strip().upper())
        return "+".join(parts)

    def _on_hotkey_letter(self, text: str) -> None:
        """Force the key field to a single uppercase letter, then apply."""
        upper = text.upper()
        if upper != text:
            blocked = self._hotkey_key.blockSignals(True)
            self._hotkey_key.setText(upper)
            self._hotkey_key.blockSignals(blocked)
        self._apply_hotkey()

    def _apply_hotkey(self) -> None:
        """Validate the widgets and ask the window to apply the combo.

        Invalid input (no modifier, no letter, digits/symbols) never reaches
        the window: the previous binding stays and the hint says why.
        """
        binding = hotkeybinding.parse(self._hotkey_text())
        if binding is None:
            self._set_hotkey_hint(
                "Global hotkey needs at least one modifier (Ctrl, Alt or "
                "Shift) and one letter A to Z.", error=True)
            return
        apply = self._actions.get("set_hotkey")
        if apply is None:
            return
        ok, error = apply(binding.format())
        if ok:
            self.set_hotkey(binding.format())
        else:
            self._set_hotkey_hint(
                error or "That combination is not available.", error=True)

    def set_hotkey(self, text: str) -> None:
        """Sync the widgets with *text* (startup / after a successful apply).

        Signals are blocked so this stays a state update: it must not call
        the window's set_hotkey action again.
        """
        binding = hotkeybinding.parse(text)
        if binding is None:
            return
        widgets = [self._hotkey_mods["ctrl"], self._hotkey_mods["alt"],
                   self._hotkey_mods["shift"], self._hotkey_key]
        blocked = [w.blockSignals(True) for w in widgets]
        self._hotkey_mods["ctrl"].setChecked(binding.ctrl)
        self._hotkey_mods["alt"].setChecked(binding.alt)
        self._hotkey_mods["shift"].setChecked(binding.shift)
        self._hotkey_key.setText(binding.key)
        for widget, previous in zip(widgets, blocked):
            widget.blockSignals(previous)
        self._set_hotkey_hint(f"Global hotkey: {binding.format()}")

    def _set_hotkey_hint(self, text: str, error: bool = False) -> None:
        self._hotkey_hint_text = text
        self._hotkey_hint_error = error
        self._restyle_hotkey_hint()
