p = "src/mewgenics_overlay/ui/settings_tab.py"
s = open(p).read()

# Save slots: three buttons side by side; open-other stays below
old = """        for n in range(3):
            btn = QPushButton(f"Slot {n + 1}")
            btn.setToolTip(_wt("Load this campaign slot's save. Slots that "
                               "don't exist yet are disabled."))
            btn.clicked.connect(
                lambda _=False, idx=n: self._actions["load_slot"](idx))
            btn.setEnabled(False)
            self._slot_buttons.append(btn)
            lay.addWidget(btn)
        row = QHBoxLayout()"""
new = """        slot_row = QHBoxLayout()
        for n in range(3):
            btn = QPushButton(f"Slot {n + 1}")
            btn.setToolTip(_wt("Load this campaign slot's save. Slots that "
                               "don't exist yet are disabled."))
            btn.clicked.connect(
                lambda _=False, idx=n: self._actions["load_slot"](idx))
            btn.setEnabled(False)
            self._slot_buttons.append(btn)
            slot_row.addWidget(btn, 1)
        lay.addLayout(slot_row)
        row = QHBoxLayout()"""
assert old in s
s = s.replace(old, new, 1)

# Theme: two selectable buttons, active highlighted
old = """        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self._theme_label = QLabel("")
        self._theme_label.setObjectName("muted")
        theme_btn = QPushButton("◐ Switch theme")
        theme_btn.setToolTip(_wt("Switch between the two noir looks (dark "
                                 "default, bright alt)."))
        theme_btn.clicked.connect(lambda: actions["theme"]())
        theme_row.addWidget(self._theme_label)
        theme_row.addStretch(1)
        theme_row.addWidget(theme_btn)
        lay.addLayout(theme_row)"""
new = """        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self._theme_buttons = {}
        for key in ("noir", "film"):
            b = QPushButton()
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key:
                              actions["set_theme"](k))
            b.setStyleSheet(
                "QPushButton { padding: 3px 12px; border: 1px solid "
                "#6a6761; border-radius: 6px; background: #24211d; }\n"
                "QPushButton:checked { border: 2px solid #7fd0a0; "
                "color: #7fd0a0; font-weight: 600; }")
            self._theme_buttons[key] = b
            theme_row.addWidget(b)
        theme_row.addStretch(1)
        lay.addLayout(theme_row)"""
assert old in s
s = s.replace(old, new, 1)

# no more stray hint line under zoom
old = """        lay.addLayout(zoom_row)
        self._zoom_hint = QLabel("")
        self._zoom_hint.setObjectName("muted")
        lay.addWidget(self._zoom_hint)
        root.addWidget(box)"""
new = """        lay.addLayout(zoom_row)
        root.addWidget(box)"""
assert old in s
s = s.replace(old, new, 1)

# Help & info group: neutral title, About + Report only
old = """        box = QGroupBox("Help & info")"""
new = """        box = QGroupBox("About & support")"""
assert old in s
s = s.replace(old, new, 1)

# replace theme/zoom state setters with segmented-button versions
old = """    # ── state updates from the window ─────────────────────────────────────
    def set_theme_text(self, name: str) -> None:
        self._theme_label.setText(name)

    def set_zoom(self, pct: int, hint: str = "") -> None:
        self._zoom_label.setText(f"{pct}%")
        self._zoom_hint.setText(hint)"""
new = """    # ── state updates from the window ─────────────────────────────────────
    def set_active_theme(self, key: str, title: str) -> None:
        for k, b in self._theme_buttons.items():
            b.setText(title if k == key else _title(k))
            b.setChecked(k == key)

    def set_theme_titles(self, titles: dict) -> None:
        self._titles = titles

    def set_zoom(self, pct: int) -> None:
        self._zoom_label.setText(f"{pct}%")"""
assert old in s
s = s.replace(old, new, 1)

# helper for theme titles + initialize on first paint
old = "class SettingsTab(QWidget):"
new = '''def _title(key: str) -> str:
    return _TITLES.get(key, key)


_TITLES: dict = {}


class SettingsTab(QWidget):'''
assert old in s
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("settings tab reworked")
