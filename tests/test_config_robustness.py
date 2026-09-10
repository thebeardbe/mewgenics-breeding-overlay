"""Config-file robustness tests (no Qt needed).

config.json lives in a user-writable directory and is plain JSON, so a
corrupt/tampered file must never crash the overlay at startup (e.g. via
``QRect(*window_rect)`` or ``int(max_partners)``). Every value is coerced
against DEFAULTS on load; this file pins that behaviour.
"""

import json

import pytest

from mewgenics_overlay.ui import config as cfg


@pytest.fixture()
def isolated_cfg(tmp_path, monkeypatch):
    """Point XDG_CONFIG_HOME at a throwaway dir and write a config file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = tmp_path / "mewgenics-overlay"
    root.mkdir()
    path = root / "config.json"

    def write(payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    return write


def test_garbage_types_fall_back_to_defaults(isolated_cfg):
    isolated_cfg({
        "theme": 123,
        "save_path": {"not": "a path"},
        "include_adventure": "false",   # the string, not the bool
        "order": ["risk"],
        "max_partners": "a hundred",
        "report_url": 42,
        "window_rect": ["a", "b", 0, "d"],
    })
    data = cfg.load()
    assert data["theme"] == cfg.DEFAULTS["theme"]
    assert data["save_path"] is None
    assert data["include_adventure"] is False   # "false" string parsed as False
    assert data["order"] == cfg.DEFAULTS["order"]
    assert data["max_partners"] == cfg.DEFAULTS["max_partners"]
    assert data["report_url"] is None
    assert data["window_rect"] is None


def test_window_rect_is_normalized(isolated_cfg):
    isolated_cfg({"window_rect": [10.9, 20.1, 880.7, 600.4]})
    assert cfg.load()["window_rect"] == [10, 20, 880, 600]

    for bad in ([0, 0, 0, 600], [10, 20, -5, 600], ["x", 0, 1, 1],
                {"x": 1}, 5):
        isolated_cfg({"window_rect": bad})
        assert cfg.load()["window_rect"] is None


def test_bool_strings_and_ints_coerce(isolated_cfg):
    isolated_cfg({"include_adventure": "true", "max_partners": "7",
                  "theme": "film"})
    data = cfg.load()
    assert data["include_adventure"] is True
    assert data["max_partners"] == 7
    assert data["theme"] == "film"


def test_max_partners_is_clamped(isolated_cfg):
    isolated_cfg({"max_partners": 99999})
    assert cfg.load()["max_partners"] == 500
    isolated_cfg({"max_partners": 0})
    assert cfg.load()["max_partners"] == 1


def test_runtime_owned_keys_pass_through(isolated_cfg):
    isolated_cfg({"pinned": {"save1": ["uid-a", "uid-b"]},
                  "some_future_key": {"nested": True}})
    data = cfg.load()
    assert data["pinned"] == {"save1": ["uid-a", "uid-b"]}
    assert data["some_future_key"] == {"nested": True}


def test_missing_file_returns_defaults(isolated_cfg):
    data = cfg.load()
    assert data == cfg.DEFAULTS


# ── global hotkey coercion ──────────────────────────────────────────────────
def test_hotkey_default_is_the_shipped_combo():
    from mewgenics_overlay.ui import hotkeybinding

    assert cfg.DEFAULTS["hotkey"] == hotkeybinding.DEFAULT_TEXT
    assert cfg.DEFAULTS["hotkey"] == "Ctrl+Shift+B"


def test_missing_hotkey_falls_back_to_the_default(isolated_cfg):
    isolated_cfg({})
    assert cfg.load()["hotkey"] == "Ctrl+Shift+B"


@pytest.mark.parametrize("saved,canonical", [
    ("Ctrl+Shift+B", "Ctrl+Shift+B"),
    ("ctrl+shift+b", "Ctrl+Shift+B"),        # lower case
    ("Ctrl + Shift + b", "Ctrl+Shift+B"),    # odd spacing + lower key
    ("  Ctrl+Alt+K  ", "Ctrl+Alt+K"),        # surrounding whitespace
    ("Control+Z", "Ctrl+Z"),                 # Windows alias
    ("B+Shift+Ctrl", "Ctrl+Shift+B"),        # token order canonicalised
    ("alt+a", "Alt+A"),
])
def test_hotkey_is_validated_and_canonicalised(isolated_cfg, saved, canonical):
    isolated_cfg({"hotkey": saved})
    assert cfg.load()["hotkey"] == canonical


@pytest.mark.parametrize("junk", [
    123, None, [], {}, True,
    "", "   ", "B", "Ctrl", "Ctrl+", "Ctrl+Shift+1",
    "Ctrl+Shift+AB", "Ctrl+Ctrl+B", "Ctrl+Shift+?", "no modifier",
])
def test_junk_hotkey_falls_back_to_the_default(isolated_cfg, junk):
    isolated_cfg({"hotkey": junk})
    assert cfg.load()["hotkey"] == "Ctrl+Shift+B"


def test_invalid_hotkey_is_logged_when_replaced(isolated_cfg, caplog):
    isolated_cfg({"hotkey": "not a combo"})

    with caplog.at_level("WARNING", logger="mewgenics_overlay.config"):
        cfg.load()

    assert any("hotkey" in r.message and "invalid" in r.message.lower()
               for r in caplog.records)
