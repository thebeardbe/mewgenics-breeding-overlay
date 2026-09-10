"""Global-hotkey combination: parsing, formatting and Win32 encoding.

Deliberately **Qt-free and ctypes-free** so ``ui/config.py`` can validate a
saved combination while the settings are loaded, before any QApplication (or
Windows DLL) exists, and so the parsing rules are unit-testable everywhere.

The canonical form is ``Ctrl+Alt+Shift+Key`` (the order is fixed; only the
modifiers actually held appear). The key is a single letter A-Z, stored
uppercase; the Win32 virtual-key code of such a letter is simply its ordinal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_TEXT = "Ctrl+Shift+B"

# Win32 RegisterHotKey modifier flags (winuser.h). Named here, not in the
# Qt-facing hotkey module, so the binding and the registration agree on one
# source of truth and no hex literal leaks into the UI code.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000   # suppress auto-repeat while the combo is held

_KEY_LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# Spoken modifier -> canonical spelling; "control" is the Windows-alias of
# Ctrl users see in other hotkey fields.
_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
}
_MODIFIER_LABELS = (("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"))


@dataclass(frozen=True)
class HotkeyBinding:
    """One global-hotkey combination (a subset of Ctrl/Alt/Shift + a letter).

    Frozen so a binding in the settings dict can never be mutated in place
    under the register/unregister path; ``key`` is always an uppercase A-Z
    letter.
    """

    ctrl: bool
    alt: bool
    shift: bool
    key: str

    def __post_init__(self) -> None:
        key = str(self.key).strip().upper()
        if key not in _KEY_LETTERS:
            raise ValueError(
                f"hotkey key must be one letter A-Z, got {self.key!r}")
        object.__setattr__(self, "key", key)

    def format(self) -> str:
        """Canonical text, always ``Ctrl+Alt+Shift+Key`` in that order."""
        parts = [label for name, label in _MODIFIER_LABELS
                 if getattr(self, name)]
        parts.append(self.key)
        return "+".join(parts)

    def vk(self) -> int:
        """The Win32 virtual-key code (a letter's ordinal)."""
        return ord(self.key)

    def mods(self) -> int:
        """Win32 modifier flags, always including MOD_NOREPEAT."""
        flags = MOD_NOREPEAT
        if self.ctrl:
            flags |= MOD_CONTROL
        if self.alt:
            flags |= MOD_ALT
        if self.shift:
            flags |= MOD_SHIFT
        return flags

    # ── desktop-environment syntaxes (single source for the conversions) ──
    # The Linux desktop shortcut manager needs the same combination in the
    # syntax each environment expects. Keeping the mapping here means the
    # Windows registration, the GTK/KDE/Hyprland strings and the config all
    # agree on one representation.
    def gtk_accelerator(self) -> str:
        """GTK accelerator syntax (GNOME custom keybindings), e.g.
        ``<Control><Shift>B``."""
        parts = []
        if self.ctrl:
            parts.append("<Control>")
        if self.alt:
            parts.append("<Alt>")
        if self.shift:
            parts.append("<Shift>")
        parts.append(self.key)
        return "".join(parts)

    def kde_shortcut(self) -> str:
        """KDE global-accelerator syntax, e.g. ``Ctrl+Shift+B``."""
        return self.format()

    def hypr_combo(self) -> str:
        """Hyprland ``bind`` combo, e.g. ``CTRL SHIFT, B``."""
        mods = []
        if self.ctrl:
            mods.append("CTRL")
        if self.alt:
            mods.append("ALT")
        if self.shift:
            mods.append("SHIFT")
        return f"{' '.join(mods)}, {self.key}"


def parse(text: object) -> Optional[HotkeyBinding]:
    """Build a binding from *text*, or return ``None`` if it is not a valid
    combination.

    Case-insensitive and whitespace-tolerant (``"ctrl + shift + b"`` works);
    ``Control`` is accepted as an alias for Ctrl. A combination needs at
    least one modifier and exactly one letter A-Z; anything else (empty, no
    modifier, two letters, digits, symbols, extra tokens) is rejected.
    """
    if not isinstance(text, str):
        return None
    tokens = [tok.strip() for tok in text.split("+")]
    if any(not tok for tok in tokens):
        return None
    mods: set[str] = set()
    key: Optional[str] = None
    for tok in tokens:
        name = _MODIFIER_ALIASES.get(tok.lower())
        if name is not None:
            if name in mods:        # "Ctrl+Ctrl+B" is not a real combo
                return None
            mods.add(name)
            continue
        if len(tok) == 1 and tok.isascii() and tok.isalpha():
            if key is not None:     # two letters
                return None
            key = tok.upper()
            continue
        return None
    if not mods or key is None:
        return None
    return HotkeyBinding(ctrl="ctrl" in mods, alt="alt" in mods,
                         shift="shift" in mods, key=key)


def binding_or_default(text: object) -> HotkeyBinding:
    """*text* parsed, or the shipped default when it is unusable.

    Single fallback point so config loading, startup and rebinding never
    disagree about what "no valid combo" means (there is no fallback list:
    the user picks the combination).
    """
    return parse(text) or DEFAULT_BINDING


DEFAULT_BINDING = parse(DEFAULT_TEXT)
assert DEFAULT_BINDING is not None, "DEFAULT_TEXT must be a valid binding"
