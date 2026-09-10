"""``ui/hotkeybinding.py``: parsing, formatting and Win32 encoding of a combo.

The module is deliberately Qt-free and ctypes-free, so these tests need no
QApplication and no Windows: they pin the canonical text form, the accepted
spellings, every rejection class and the modifier/virtual-key encoding that
``ui/hotkey.py`` feeds to ``RegisterHotKey``.
"""

from __future__ import annotations

import dataclasses

import pytest

from mewgenics_overlay.ui import hotkeybinding as hb
from mewgenics_overlay.ui.hotkeybinding import HotkeyBinding


# ── 1. the shipped default ─────────────────────────────────────────────────
def test_default_text_and_binding_agree():
    assert hb.DEFAULT_TEXT == "Ctrl+Shift+B"
    assert hb.DEFAULT_BINDING == HotkeyBinding(ctrl=True, alt=False,
                                               shift=True, key="B")
    assert hb.DEFAULT_BINDING.format() == hb.DEFAULT_TEXT


# ── 2. parse / format round-trips ──────────────────────────────────────────
@pytest.mark.parametrize("text,canonical", [
    ("Ctrl+Shift+B", "Ctrl+Shift+B"),
    ("ctrl+shift+b", "Ctrl+Shift+B"),
    ("CTRL+SHIFT+B", "Ctrl+Shift+B"),
    ("Ctrl + Shift + b", "Ctrl+Shift+B"),
    ("  Ctrl+Shift+B  ", "Ctrl+Shift+B"),
    ("Control+Shift+B", "Ctrl+Shift+B"),       # Control is the Windows alias
    ("B+Shift+Ctrl", "Ctrl+Shift+B"),          # token order is canonicalised
    ("Alt+A", "Alt+A"),
    ("Ctrl+Alt+K", "Ctrl+Alt+K"),
    ("Ctrl+Alt+Shift+Z", "Ctrl+Alt+Shift+Z"),
    ("Shift+Alt+Ctrl+Z", "Ctrl+Alt+Shift+Z"),
])
def test_parse_formats_to_the_canonical_text(text, canonical):
    binding = hb.parse(text)
    assert binding is not None
    assert binding.format() == canonical


@pytest.mark.parametrize("text", [
    "Ctrl+Shift+B", "Alt+A", "Ctrl+Alt+K", "Control+Z",
])
def test_format_round_trips_back_through_parse(text):
    binding = hb.parse(text)
    assert binding is not None
    assert hb.parse(binding.format()) == binding


def test_parse_is_case_insensitive_for_the_key():
    assert hb.parse("Ctrl+Shift+b").key == "B"
    assert hb.parse("Ctrl+Shift+B").key == "B"


# ── 3. every rejection class ───────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    None,
    0,
    123,
    True,
    [],
    {},
    b"Ctrl+Shift+B",
])
def test_non_string_input_is_rejected(text):
    assert hb.parse(text) is None


@pytest.mark.parametrize("text", [
    "",                       # empty
    "   ",                    # only whitespace
    "B",                      # no modifier
    "A+B",                    # two keys, no modifier
    "Ctrl",                   # modifier but no key
    "Ctrl+Shift",             # modifiers but no key
    "Ctrl+",                  # dangling separator
    "+B",                     # leading separator
    "Ctrl++B",                # doubled separator
    "Ctrl+Shift+",            # trailing separator
    "Ctrl Ctrl B",            # missing + between modifiers
    "Ctrl+Shift+AB",          # two letters
    "Ctrl+Shift+ABC",         # three letters
    "Ctrl+Shift+1",           # digit
    "Ctrl+Shift+0",           # zero
    "Ctrl+Shift+9",           # nine
    "Ctrl+Shift+?",           # symbol
    "Ctrl+Shift+ ",           # blank key
    "Ctrl+Ctrl+B",            # duplicated modifier
    "Alt+Alt+B",              # duplicated modifier (different token case)
    "Ctrl+Shift+Shift+B",     # duplicated modifier
    "Ctrl+Shift+B+Shift",     # duplicate after the key
    "Fn+Shift+B",             # unknown modifier token
    "Ctrl+Shift+ß",           # non-ASCII letter
    "Ctrl+Shift+é",           # non-ASCII letter
    "Ctrl+Shift+F1",          # function key
    "Ctrl+Shift+Enter",       # named key
    "Ctrl+Shift+\t",          # control character
])
def test_invalid_combinations_are_rejected(text):
    assert hb.parse(text) is None


def test_unicode_letter_is_not_treated_as_ascii_a_z():
    # ``str.isalpha`` is true for é, but the Win32 virtual-key encoding only
    # covers A-Z; the parser must not pretend otherwise.
    assert "é".isalpha() and not "é".isascii()
    assert hb.parse("Ctrl+é") is None


# ── 4. virtual-key and modifier encoding ───────────────────────────────────
@pytest.mark.parametrize("key,vk", [
    ("A", 65),
    ("B", 66),
    ("M", 77),
    ("Z", 90),
])
def test_vk_is_the_letter_ordinal(key, vk):
    assert HotkeyBinding(True, False, False, key).vk() == vk


def test_win32_modifier_constants_match_winuser_h():
    assert hb.MOD_ALT == 0x0001
    assert hb.MOD_CONTROL == 0x0002
    assert hb.MOD_SHIFT == 0x0004
    assert hb.MOD_NOREPEAT == 0x4000


@pytest.mark.parametrize("ctrl,alt,shift,expected_extra", [
    (True, False, False, hb.MOD_CONTROL),
    (False, True, False, hb.MOD_ALT),
    (False, False, True, hb.MOD_SHIFT),
    (True, True, False, hb.MOD_CONTROL | hb.MOD_ALT),
    (True, False, True, hb.MOD_CONTROL | hb.MOD_SHIFT),
    (False, True, True, hb.MOD_ALT | hb.MOD_SHIFT),
    (True, True, True, hb.MOD_CONTROL | hb.MOD_ALT | hb.MOD_SHIFT),
])
def test_mods_sets_the_held_flags_plus_norepeat(ctrl, alt, shift,
                                                expected_extra):
    flags = HotkeyBinding(ctrl, alt, shift, "K").mods()
    assert flags == hb.MOD_NOREPEAT | expected_extra


def test_mod_norepeat_is_always_set_even_without_modifiers():
    # Direct construction may omit every modifier (``parse`` rejects it);
    # the encoding still suppresses auto-repeat.
    binding = HotkeyBinding(False, False, False, "K")
    assert binding.mods() == hb.MOD_NOREPEAT
    assert binding.format() == "K"


# ── 5. the dataclass itself ────────────────────────────────────────────────
def test_key_is_canonicalised_to_uppercase_on_construction():
    assert HotkeyBinding(True, False, False, " q ").key == "Q"
    assert HotkeyBinding(True, False, False, "z").format() == "Ctrl+Z"


@pytest.mark.parametrize("bad_key", ["", "AB", "1", "?", "é", None, 7])
def test_invalid_key_raises_value_error(bad_key):
    with pytest.raises(ValueError):
        HotkeyBinding(True, False, False, bad_key)


def test_binding_is_frozen():
    binding = HotkeyBinding(True, False, True, "B")
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.ctrl = False


def test_binding_equality_and_hash_use_all_fields():
    a = HotkeyBinding(True, False, True, "B")
    b = HotkeyBinding(True, False, True, "B")
    c = HotkeyBinding(True, True, True, "B")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert len({a, b, c}) == 2


# ── 6. binding_or_default, the single fallback point ───────────────────────
@pytest.mark.parametrize("text,canonical", [
    ("Ctrl+Alt+K", "Ctrl+Alt+K"),
    ("ctrl + alt + k", "Ctrl+Alt+K"),
])
def test_binding_or_default_uses_a_valid_text(text, canonical):
    assert hb.binding_or_default(text).format() == canonical


@pytest.mark.parametrize("junk", [
    None, 0, "", "B", "Ctrl", "Ctrl+Shift+1", "garbage", "Ctrl+Ctrl+B",
    b"Ctrl+Shift+B", [],
])
def test_binding_or_default_falls_back_to_the_default(junk):
    assert hb.binding_or_default(junk) is hb.DEFAULT_BINDING


def test_binding_or_default_fallback_is_the_shared_default_object():
    # One source of truth: callers compare against DEFAULT_BINDING.
    assert hb.binding_or_default("junk") is hb.DEFAULT_BINDING
    assert hb.binding_or_default(123) is hb.binding_or_default(None)
