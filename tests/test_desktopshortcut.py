"""``ui/desktopshortcut.py`` + ``ui/shortcut_backends.py``: Linux shortcut setup.

Everything here is driven through an injected command runner and temporary
directories, so no real ``gsettings``/``qdbus``/``hyprctl`` is ever executed
and the user's real config is never touched (``XDG_DATA_HOME`` and
``XDG_CONFIG_HOME`` point into ``tmp_path``). Each backend install/remove path
is checked for the exact commands it runs, the files it writes and the safety
markers that keep it away from the user's own shortcuts.

The backends receive the **executable path**, not a prebuilt command string;
each consumer's quoting (shell for GNOME/Hyprland, Desktop-Entry double
quotes for KDE) is built by the one shared helper in ``shortcut_common``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mewgenics_overlay.ui import desktopshortcut
from mewgenics_overlay.ui import shortcut_backends
from mewgenics_overlay.ui import shortcut_common
from mewgenics_overlay.ui import shortcut_hyprland
from mewgenics_overlay.ui.hotkeybinding import HotkeyBinding
from mewgenics_overlay.ui.shortcut_common import (
    APP_NAME,
    APP_SLUG,
    DESKTOP_MARKER,
    GNOME,
    HYPRLAND,
    HYPR_MARKER,
    KDE,
    OTHER,
    CommandResult,
)

# Private constants are the contract these tests pin (the strings users see).
_GNOME_MEDIA = shortcut_backends._GNOME_MEDIA_KEYS
_GNOME_SCHEMA = shortcut_backends._GNOME_SCHEMA
_GNOME_PATH = shortcut_backends._GNOME_CUSTOM_PATH
_KDE_SERVICE = shortcut_backends._KDE_SERVICE
_KDE_PATH = shortcut_backends._KDE_PATH
_KDE_IFACE = shortcut_backends._KDE_IFACE
_HYPR_BACKUP = shortcut_hyprland._HYPR_BACKUP_SUFFIX


# ── fakes / helpers ────────────────────────────────────────────────────────
class FakeRunner:
    """Records every argv it is handed; returns queued or handler results."""

    def __init__(self, handler=None, results=None):
        self.calls: list[list[str]] = []
        self._handler = handler
        self._results = list(results or [])

    def __call__(self, argv):
        call = [str(arg) for arg in argv]
        self.calls.append(call)
        if self._handler is not None:
            return self._handler(call)
        if self._results:
            return self._results.pop(0)
        return CommandResult(True)


def _which(mapping):
    """``shutil.which`` stand-in: only the mapped tool names exist."""
    return lambda name: mapping.get(name)


def _clear_desktop_env(monkeypatch):
    for var in ("XDG_CURRENT_DESKTOP", "HYPRLAND_INSTANCE_SIGNATURE",
                "XDG_SESSION_TYPE"):
        monkeypatch.delenv(var, raising=False)


def _binding(ctrl=True, alt=False, shift=True, key="B"):
    return HotkeyBinding(ctrl, alt, shift, key)


def _backup_of(conf):
    return conf.with_name(conf.name + _HYPR_BACKUP)


# A path that exercises every character the Desktop Entry spec reserves inside
# a double-quoted Exec argument (backslash, double quote, backtick, dollar).
_HOSTILE_PATH = '/opt/a\\b"c`d$e'


@pytest.fixture
def kde_home(monkeypatch, tmp_path):
    """Point the KDE launcher path at a throwaway XDG data home."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data" / "applications" / f"{APP_SLUG}.desktop"


@pytest.fixture
def hypr_conf(monkeypatch, tmp_path):
    """A throwaway hyprland.conf with an existing foreign bind line."""
    conf_dir = tmp_path / "hypr"
    conf_dir.mkdir()
    conf = conf_dir / "hyprland.conf"
    conf.write_text("# user config\nbind = SUPER, Q, exec, kitty\n",
                    encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return conf


# ── 1. detect_desktop ──────────────────────────────────────────────────────
@pytest.mark.parametrize("env,expected", [
    ({"XDG_CURRENT_DESKTOP": "GNOME"}, GNOME),
    ({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, GNOME),
    ({"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, GNOME),
    ({"XDG_CURRENT_DESKTOP": "Unity"}, GNOME),
    ({"XDG_CURRENT_DESKTOP": "KDE"}, KDE),
    ({"XDG_CURRENT_DESKTOP": "plasma"}, KDE),
    ({"XDG_CURRENT_DESKTOP": "KDE-Plasma"}, KDE),
    ({"HYPRLAND_INSTANCE_SIGNATURE": "abc123"}, HYPRLAND),
])
def test_detect_desktop_from_environment(monkeypatch, env, expected):
    _clear_desktop_env(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert desktopshortcut.detect_desktop() == expected


def test_detect_desktop_falls_back_to_installed_tools(monkeypatch):
    _clear_desktop_env(monkeypatch)

    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/gsettings"
                        if name == "gsettings" else None)
    assert desktopshortcut.detect_desktop() == GNOME

    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/qdbus6"
                        if name == "qdbus6" else None)
    assert desktopshortcut.detect_desktop() == KDE

    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/hyprctl"
                        if name == "hyprctl" else None)
    assert desktopshortcut.detect_desktop() == HYPRLAND

    monkeypatch.setattr("shutil.which", lambda name: None)
    assert desktopshortcut.detect_desktop() == OTHER


def test_detect_desktop_ignores_xdg_session_type(monkeypatch):
    # The session type only says wayland/x11, not which desktop; it must not
    # make an otherwise-unknown session look like a supported one.
    _clear_desktop_env(monkeypatch)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert desktopshortcut.detect_desktop() == OTHER


def test_desktop_label_names_each_backend():
    assert desktopshortcut.desktop_label(GNOME) == "GNOME"
    assert desktopshortcut.desktop_label(KDE) == "KDE Plasma"
    assert desktopshortcut.desktop_label(HYPRLAND) == "Hyprland"
    assert desktopshortcut.desktop_label(OTHER) == "this desktop"


# ── 2. command_path / toggle_command ───────────────────────────────────────
def _executable(tmp_path, name=APP_SLUG):
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_command_path_prefers_an_executable_argv0(monkeypatch, tmp_path):
    exe = _executable(tmp_path)
    monkeypatch.setattr("sys.argv", [str(exe)])
    assert desktopshortcut.command_path() == os.path.realpath(str(exe))


def test_command_path_skips_a_module_entry_point(monkeypatch):
    # ``python -m mewgenics_overlay`` sets argv[0] to __main__.py, which is
    # not the command a shortcut may run.
    monkeypatch.setattr("sys.argv", ["/some/dir/__main__.py"])
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert desktopshortcut.command_path() == APP_SLUG


def test_command_path_falls_back_to_the_console_script(monkeypatch, tmp_path):
    exe = _executable(tmp_path)
    monkeypatch.setattr("sys.argv", ["/some/dir/__main__.py"])
    monkeypatch.setattr("shutil.which",
                        lambda name: str(exe) if name == APP_SLUG else None)
    assert desktopshortcut.command_path() == os.path.realpath(str(exe))


def test_command_path_uses_argv0_when_it_exists_but_is_not_executable(
        monkeypatch, tmp_path):
    plain = tmp_path / "mewgenics-overlay"
    plain.write_text("not executable\n", encoding="utf-8")
    plain.chmod(0o644)
    monkeypatch.setattr("sys.argv", [str(plain)])
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert desktopshortcut.command_path() == os.path.abspath(str(plain))


def test_command_path_without_argv_is_the_app_slug(monkeypatch):
    monkeypatch.setattr("sys.argv", [])
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert desktopshortcut.command_path() == APP_SLUG


def test_toggle_command_appends_the_flag(monkeypatch, tmp_path):
    exe = _executable(tmp_path)
    monkeypatch.setattr("sys.argv", [str(exe)])
    assert desktopshortcut.toggle_command() == \
        f"{os.path.realpath(str(exe))} --toggle"


def test_toggle_command_default_is_the_plain_form():
    assert shortcut_common.toggle_command("/opt/app") == "/opt/app --toggle"


@pytest.mark.parametrize("quote,expected", [
    (shortcut_common.QUOTE_PLAIN, "/opt/my app --toggle"),
    (shortcut_common.QUOTE_SHELL, "'/opt/my app' --toggle"),
    (shortcut_common.QUOTE_DESKTOP, '"/opt/my app" --toggle'),
])
def test_toggle_command_quotes_the_path_for_each_consumer(quote, expected):
    assert shortcut_common.toggle_command("/opt/my app", quote) == expected


def test_toggle_command_shell_quoting_neutralises_metacharacters():
    # One shared helper builds every bound command, so a hostile path cannot
    # smuggle a second command into a shell-parsed value.
    cmd = shortcut_common.toggle_command("/opt/a; rm -rf x",
                                         shortcut_common.QUOTE_SHELL)
    assert cmd == "'/opt/a; rm -rf x' --toggle"


def test_desktop_quote_doubles_percent_field_codes():
    # ``%f``/``%u``/``%c``/``%k`` are Desktop Entry field codes a launcher
    # expands; a literal percent in the executable path must be doubled rather
    # than backslash-escaped, or the launcher substitutes the field code.
    path = "/opt/pct%f%u%c%k"
    assert shortcut_common.toggle_command(
        path, shortcut_common.QUOTE_DESKTOP) == \
        '"/opt/pct%%f%%u%%c%%k" --toggle'


def test_percent_is_literal_in_the_shell_and_plain_forms():
    # Only the Desktop Entry form reserves percent; the GNOME/Hyprland shell
    # value and the informational plain value keep it exactly as written. A
    # space forces the shell form to quote, so the quoted percent is checked.
    path = "/opt/my pct%f%u%c%k"
    assert shortcut_common.toggle_command(
        path, shortcut_common.QUOTE_SHELL) == f"'{path}' --toggle"
    assert shortcut_common.toggle_command(
        path, shortcut_common.QUOTE_PLAIN) == f"{path} --toggle"


# ── 3. GNOME / gsettings ───────────────────────────────────────────────────
def test_gnome_install_appends_our_path_and_sets_every_key():
    existing = ("['/org/gnome/settings-daemon/plugins/media-keys/"
                "custom-keybindings/custom0/']")
    runner = FakeRunner(results=[CommandResult(True, stdout=existing)])
    ok, message = shortcut_backends.install_for(
        GNOME, _binding(True, True, False, "K"), "/opt/app", runner)

    assert ok is True
    assert runner.calls[0][:3] == ["gsettings", "get", _GNOME_MEDIA]
    set_list = runner.calls[1]
    assert set_list[:4] == ["gsettings", "set", _GNOME_MEDIA,
                            "custom-keybindings"]
    assert "custom0" in set_list[4]          # the user's path is kept
    assert _GNOME_PATH in set_list[4]        # ours is appended
    assert ["gsettings", "set", _GNOME_SCHEMA, "name", APP_NAME] in runner.calls
    assert ["gsettings", "set", _GNOME_SCHEMA, "command",
            "/opt/app --toggle"] in runner.calls
    assert ["gsettings", "set", _GNOME_SCHEMA, "binding",
            "<Control><Alt>K"] in runner.calls


def test_gnome_install_is_idempotent_for_our_path():
    existing = (f"['{_GNOME_PATH}', "
                "'/org/gnome/settings-daemon/plugins/media-keys/"
                "custom-keybindings/custom0/']")
    runner = FakeRunner(results=[CommandResult(True, stdout=existing)])
    shortcut_backends.install_for(GNOME, _binding(), "/opt/app", runner)

    set_list = runner.calls[1]
    assert set_list[4].count(_GNOME_PATH) == 1


def test_gnome_install_shell_quotes_a_path_with_spaces():
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    ok, _ = shortcut_backends.install_for(GNOME, _binding(), "/opt/my app",
                                          runner)

    assert ok is True
    assert ["gsettings", "set", _GNOME_SCHEMA, "command",
            "'/opt/my app' --toggle"] in runner.calls


def test_gnome_install_without_the_schema_reports_it_clearly():
    runner = FakeRunner(results=[CommandResult(False, stderr="No such schema")])
    ok, message = shortcut_backends.install_for(
        GNOME, _binding(), "/opt/app", runner)

    assert ok is False
    assert "schema" in message.lower()
    assert "Settings" in message
    # Only the read ran; nothing was written.
    assert len(runner.calls) == 1


def test_gnome_install_aborts_on_a_parse_failure_without_writing():
    # A saved value that cannot be parsed is not the same as an empty list:
    # silently treating it as empty would drop the user's other bindings.
    runner = FakeRunner(results=[CommandResult(True, stdout="not a list")])

    ok, message = shortcut_backends.install_for(GNOME, _binding(), "/opt/app",
                                                runner)

    assert ok is False
    assert "could not be read" in message
    # Only the failing read ran; no ``set`` was attempted.
    assert len(runner.calls) == 1
    assert not any(call[:2] == ["gsettings", "set"] for call in runner.calls)


def test_gnome_remove_aborts_on_a_parse_failure_without_writing():
    runner = FakeRunner(results=[CommandResult(True, stdout="!! garbage")])

    ok, message = shortcut_backends.remove_for(GNOME, runner)

    assert ok is False
    assert "unavailable" in message.lower()
    assert len(runner.calls) == 1


def test_gnome_empty_list_is_an_empty_value_not_a_failure():
    # Only the literal empty list is a genuine empty value; blank output is a
    # failed read (see test_gnome_blank_output_is_a_read_failure) so a caller
    # never drops keybindings it could not read.
    assert shortcut_backends._parse_gvariant_list("[]") == []
    assert shortcut_backends._parse_gvariant_list("@as []") == []
    assert shortcut_backends._parse_gvariant_list("  []  ") == []


@pytest.mark.parametrize("text", ["", "  ", "\n", "\t \n"])
def test_gnome_blank_output_is_a_read_failure(text):
    assert shortcut_backends._parse_gvariant_list(text) is None


def test_gnome_install_aborts_on_blank_output_without_writing():
    # ``gsettings get`` can exit 0 with empty stdout when the read fails;
    # treating that as "no keybindings" would drop the user's other entries.
    runner = FakeRunner(results=[CommandResult(True, stdout="  \n")])

    ok, message = shortcut_backends.install_for(GNOME, _binding(), "/opt/app",
                                                runner)

    assert ok is False
    assert "could not be read" in message
    assert len(runner.calls) == 1
    assert not any(call[:2] == ["gsettings", "set"] for call in runner.calls)


def test_gnome_remove_aborts_on_blank_output_without_writing():
    runner = FakeRunner(results=[CommandResult(True, stdout="")])

    ok, message = shortcut_backends.remove_for(GNOME, runner)

    assert ok is False
    assert "unavailable" in message.lower()
    assert len(runner.calls) == 1
    assert not any(call[:2] == ["gsettings", "set"] for call in runner.calls)
    assert not any(call[:2] == ["gsettings", "reset"] for call in runner.calls)


@pytest.mark.parametrize("text", ["not a list", "!!", "'just a string'",
                                  "{'a': 1}", "[1, 2"])
def test_gnome_parse_failure_returns_none(text):
    assert shortcut_backends._parse_gvariant_list(text) is None


def test_gnome_install_proceeds_for_an_empty_stored_list():
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    ok, _ = shortcut_backends.install_for(GNOME, _binding(), "/opt/app",
                                          runner)

    assert ok is True
    assert ["gsettings", "set", _GNOME_MEDIA, "custom-keybindings",
            f"['{_GNOME_PATH}']"] in runner.calls


def test_gnome_install_midway_failure_returns_the_manual_command():
    runner = FakeRunner(results=[
        CommandResult(True, stdout="[]"),
        CommandResult(False, stderr="permission denied"),
    ])
    ok, message = shortcut_backends.install_for(
        GNOME, _binding(), "/opt/app", runner)

    assert ok is False
    assert "permission denied" in message
    assert "gsettings set" in message


def test_gnome_remove_filters_only_our_path_and_resets_keys():
    other = ("/org/gnome/settings-daemon/plugins/media-keys/"
             "custom-keybindings/custom0/")
    runner = FakeRunner(results=[
        CommandResult(True, stdout=f"['{other}', '{_GNOME_PATH}']")])
    ok, message = shortcut_backends.remove_for(GNOME, runner)

    assert ok is True
    set_list = runner.calls[1]
    assert other in set_list[4]
    assert _GNOME_PATH not in set_list[4]
    for key in ("name", "command", "binding"):
        assert ["gsettings", "reset", _GNOME_SCHEMA, key] in runner.calls


def test_gnome_remove_is_idempotent_when_our_path_is_absent():
    other = ("/org/gnome/settings-daemon/plugins/media-keys/"
             "custom-keybindings/custom0/")
    runner = FakeRunner(results=[CommandResult(True, stdout=f"['{other}']")])
    ok, message = shortcut_backends.remove_for(GNOME, runner)

    assert ok is True
    assert not any(call[:4] == ["gsettings", "set", _GNOME_MEDIA,
                                "custom-keybindings"] for call in runner.calls)
    resets = [call for call in runner.calls
              if call[:3] == ["gsettings", "reset", _GNOME_SCHEMA]]
    assert len(resets) == 3


def test_gnome_remove_without_the_schema_reports_it_clearly():
    runner = FakeRunner(results=[CommandResult(False, stderr="No such schema")])
    ok, message = shortcut_backends.remove_for(GNOME, runner)

    assert ok is False
    assert "schema" in message.lower()
    assert len(runner.calls) == 1


# ── 4. KDE Plasma ──────────────────────────────────────────────────────────
def test_kde_install_writes_the_launcher_and_registers(
        monkeypatch, kde_home):
    monkeypatch.setattr("shutil.which", _which({"qdbus6": "/usr/bin/qdbus6"}))
    runner = FakeRunner()
    binding = _binding(True, False, True, "B")

    ok, message = shortcut_backends.install_for(
        KDE, binding, "/opt/app", runner)

    assert ok is True
    content = kde_home.read_text(encoding="utf-8")
    assert DESKTOP_MARKER in content
    assert "[Desktop Entry]" in content
    assert "X-KDE-GlobalAccel-CommandShortcut=true" in content
    assert 'Exec="/opt/app" --toggle' in content
    assert ["/usr/bin/qdbus6", _KDE_SERVICE, _KDE_PATH,
            f"{_KDE_IFACE}.doRegister", APP_SLUG, APP_SLUG] in runner.calls
    assert any(f"{_KDE_IFACE}.setShortcut" in call[3]
               and binding.kde_shortcut() in call for call in runner.calls)


def test_kde_exec_is_double_quoted_for_a_path_with_spaces(
        monkeypatch, kde_home):
    monkeypatch.setattr("shutil.which", _which({"qdbus6": "/usr/bin/qdbus6"}))

    shortcut_backends.install_for(KDE, _binding(), "/opt/my app", FakeRunner())

    content = kde_home.read_text(encoding="utf-8")
    assert 'Exec="/opt/my app" --toggle' in content


def test_kde_exec_escapes_the_reserved_desktop_entry_characters(
        monkeypatch, kde_home):
    # A backslash, double quote, backtick or ``$`` inside the quoted Exec
    # argument changes what the launcher parses (or runs), so each is
    # backslash-escaped in the exact Desktop Entry form.
    monkeypatch.setattr("shutil.which", _which({}))

    shortcut_backends.install_for(KDE, _binding(), _HOSTILE_PATH,
                                  FakeRunner())

    content = kde_home.read_text(encoding="utf-8")
    expected = ('Exec="/opt/a' + r'\\' + 'b' + r'\"' + 'c' + r'\`'
                + 'd' + r'\$' + 'e" --toggle')
    assert expected in content


def test_shell_and_plain_forms_leave_the_hostile_path_literal():
    # Only the Desktop Entry form escapes these characters; the shell-quoted
    # GNOME/Hyprland value stays the plain single-quoted path.
    assert shortcut_common.toggle_command(
        _HOSTILE_PATH, shortcut_common.QUOTE_SHELL) == \
        f"'{_HOSTILE_PATH}' --toggle"
    assert shortcut_common.toggle_command(
        _HOSTILE_PATH, shortcut_common.QUOTE_PLAIN) == \
        f"{_HOSTILE_PATH} --toggle"


def test_gnome_command_shell_quotes_the_hostile_path():
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    ok, _ = shortcut_backends.install_for(GNOME, _binding(), _HOSTILE_PATH,
                                          runner)

    assert ok is True
    assert ["gsettings", "set", _GNOME_SCHEMA, "command",
            f"'{_HOSTILE_PATH}' --toggle"] in runner.calls


def test_kde_exec_doubles_a_percent_in_the_executable_path(
        monkeypatch, kde_home):
    # The Desktop Entry ``Exec=`` line is the KDE consumer of the quoted form,
    # so a literal percent must survive as a doubled percent there too.
    monkeypatch.setattr("shutil.which", _which({}))

    shortcut_backends.install_for(KDE, _binding(), "/opt/pct%f%u%c%k",
                                  FakeRunner())

    content = kde_home.read_text(encoding="utf-8")
    assert 'Exec="/opt/pct%%f%%u%%c%%k" --toggle' in content


def test_kde_install_falls_back_to_kwriteconfig(monkeypatch, kde_home):
    monkeypatch.setattr(
        "shutil.which",
        _which({"kwriteconfig6": "/usr/bin/kwriteconfig6"}))
    runner = FakeRunner()
    binding = _binding(True, False, True, "B")
    shortcut = binding.kde_shortcut()

    ok, message = shortcut_backends.install_for(
        KDE, binding, "/opt/app", runner)

    assert ok is True
    # kglobalshortcutsrc stores "<Shortcut>\t<DefaultShortcut>\t<Name>"; the
    # binding must occupy both shortcut slots, not the app name, or the key is
    # never bound even though the write succeeds.
    assert ["/usr/bin/kwriteconfig6", "--file", "kglobalshortcutsrc",
            "--group", APP_SLUG, "--key", APP_SLUG,
            f"{shortcut}\t{shortcut}\t{APP_NAME}"] in runner.calls


def test_kde_kwriteconfig5_is_used_when_only_it_exists(monkeypatch, kde_home):
    monkeypatch.setattr(
        "shutil.which",
        _which({"kwriteconfig5": "/usr/bin/kwriteconfig5"}))
    runner = FakeRunner()
    binding = _binding()

    ok, _ = shortcut_backends.install_for(
        KDE, binding, "/opt/app", runner)

    assert ok is True
    shortcut = binding.kde_shortcut()
    assert ["/usr/bin/kwriteconfig5", "--file", "kglobalshortcutsrc",
            "--group", APP_SLUG, "--key", APP_SLUG,
            f"{shortcut}\t{shortcut}\t{APP_NAME}"] in runner.calls


def test_kde_remove_deletes_the_kwriteconfig_entry(monkeypatch, kde_home):
    monkeypatch.setattr(
        "shutil.which",
        _which({"kwriteconfig6": "/usr/bin/kwriteconfig6"}))
    shortcut_backends.install_for(KDE, _binding(), "/opt/app", FakeRunner())
    runner = FakeRunner()

    ok, _ = shortcut_backends.remove_for(KDE, runner)

    assert ok is True
    assert not kde_home.exists()
    # The same group/key install wrote is deleted, or a stale global shortcut
    # survives the launcher removal.
    assert ["/usr/bin/kwriteconfig6", "--file", "kglobalshortcutsrc",
            "--group", APP_SLUG, "--key", APP_SLUG, "--delete"] \
        in runner.calls


def test_kde_remove_reports_a_failed_kwriteconfig_delete(monkeypatch,
                                                         kde_home):
    monkeypatch.setattr(
        "shutil.which",
        _which({"kwriteconfig6": "/usr/bin/kwriteconfig6"}))
    shortcut_backends.install_for(KDE, _binding(), "/opt/app", FakeRunner())
    runner = FakeRunner(
        handler=lambda call: CommandResult(False, stderr="config locked"))

    ok, message = shortcut_backends.remove_for(KDE, runner)

    assert ok is False
    assert "config locked" in message


def test_kde_install_does_not_overwrite_a_foreign_file(monkeypatch,
                                                       kde_home):
    """A same-named launcher without our marker belongs to someone else.

    ``remove`` already refuses to delete a file it did not write (see
    ``test_kde_remove_leaves_a_foreign_file_alone``); install must be just
    as careful, or a foreign launcher at the app path is silently clobbered
    before it can ever be removed.
    """
    kde_home.parent.mkdir(parents=True, exist_ok=True)
    foreign = "[Desktop Entry]\nName=Other app\nExec=other\n"
    kde_home.write_text(foreign, encoding="utf-8")
    monkeypatch.setattr("shutil.which", _which({"qdbus6": "/usr/bin/qdbus6"}))

    shortcut_backends.install_for(KDE, _binding(), "/opt/app", FakeRunner())

    assert kde_home.read_text(encoding="utf-8") == foreign


def test_kde_failed_registration_returns_the_manual_commands(
        monkeypatch, kde_home):
    monkeypatch.setattr("shutil.which", _which({"qdbus6": "/usr/bin/qdbus6"}))

    def handler(call):
        if "doRegister" in call[3]:
            return CommandResult(False, stderr="no session")
        return CommandResult(True)

    runner = FakeRunner(handler=handler)
    binding = _binding(True, False, True, "B")
    ok, message = shortcut_backends.install_for(
        KDE, binding, "/opt/app", runner)

    assert ok is False
    assert f"{_KDE_IFACE}.doRegister" in message
    assert f"{_KDE_IFACE}.setShortcut" in message
    assert binding.kde_shortcut() in message
    assert str(kde_home) in message
    assert APP_SLUG in message


def test_kde_remove_deletes_only_our_marked_launcher(monkeypatch, kde_home):
    monkeypatch.setattr("shutil.which", _which({"qdbus6": "/usr/bin/qdbus6"}))
    shortcut_backends.install_for(KDE, _binding(), "/opt/app", FakeRunner())
    assert kde_home.exists()

    ok, message = shortcut_backends.remove_for(KDE, FakeRunner())

    assert ok is True
    assert not kde_home.exists()
    assert "removed" in message.lower()


def test_kde_remove_leaves_a_foreign_file_alone(monkeypatch, kde_home):
    kde_home.parent.mkdir(parents=True, exist_ok=True)
    foreign = "[Desktop Entry]\nName=Other app\nExec=other\n"
    kde_home.write_text(foreign, encoding="utf-8")
    # Both clearing tools exist here, so a stray delete/unregister would show
    # up: a foreign launcher means the kglobalshortcutsrc entry and the
    # registered D-Bus shortcut belong to the user and must be left alone.
    monkeypatch.setattr("shutil.which", _which({
        "kwriteconfig6": "/usr/bin/kwriteconfig6",
        "qdbus6": "/usr/bin/qdbus6",
    }))
    runner = FakeRunner()

    ok, message = shortcut_backends.remove_for(KDE, runner)

    assert ok is True
    assert kde_home.read_text(encoding="utf-8") == foreign
    assert message == shortcut_common.manual_remove(KDE)
    assert "No automatic desktop shortcut integration" in message
    # No kwriteconfig --delete and no qdbus setShortcut clearing command ran.
    assert runner.calls == []
    assert not any("--delete" in call for call in runner.calls)
    assert not any(_KDE_IFACE in " ".join(call) for call in runner.calls)


def test_kde_remove_when_nothing_is_installed(monkeypatch, kde_home):
    monkeypatch.setattr("shutil.which", _which({}))
    ok, message = shortcut_backends.remove_for(KDE, FakeRunner())
    assert ok is True
    assert "No KDE Plasma desktop shortcut was installed." == message


# ── 5. Hyprland ────────────────────────────────────────────────────────────
def test_hyprland_install_persists_one_line_applies_and_backs_up(
        monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))
    runner = FakeRunner()
    binding = _binding(True, False, True, "B")

    ok, message = shortcut_backends.install_for(
        HYPRLAND, binding, "/opt/app", runner)

    assert ok is True
    text = hypr_conf.read_text(encoding="utf-8")
    assert text.count(HYPR_MARKER) == 1
    assert ("bind = CTRL SHIFT, B, exec, /opt/app --toggle "
            f"{HYPR_MARKER}") in text
    assert "bind = SUPER, Q, exec, kitty" in text      # foreign line kept
    # ``hyprctl keyword bind`` is invalid on current Hyprland: the backend
    # reloads the whole config instead.
    assert ["/usr/bin/hyprctl", "reload"] in runner.calls
    assert not any(call[1] == "keyword" for call in runner.calls)
    backup = _backup_of(hypr_conf)
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == \
        "# user config\nbind = SUPER, Q, exec, kitty\n"
    assert "applied now" in message


def test_hyprland_exec_is_shell_quoted_for_a_path_with_spaces(
        monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))

    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/my app",
                                  FakeRunner())

    text = hypr_conf.read_text(encoding="utf-8")
    assert ("bind = CTRL SHIFT, B, exec, '/opt/my app' --toggle "
            f"{HYPR_MARKER}") in text


def test_hyprland_exec_shell_quotes_the_hostile_path(monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))

    shortcut_backends.install_for(HYPRLAND, _binding(), _HOSTILE_PATH,
                                  FakeRunner())

    text = hypr_conf.read_text(encoding="utf-8")
    assert (f"bind = CTRL SHIFT, B, exec, '{_HOSTILE_PATH}' --toggle "
            f"{HYPR_MARKER}") in text


def test_hyprland_install_never_duplicates_the_managed_line(
        monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))     # no hyprctl
    for _ in range(2):
        shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                      FakeRunner())

    text = hypr_conf.read_text(encoding="utf-8")
    assert text.count(HYPR_MARKER) == 1
    assert "bind = SUPER, Q, exec, kitty" in text


def test_hyprland_install_without_hyprctl_still_persists(monkeypatch,
                                                         hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))
    runner = FakeRunner()
    ok, message = shortcut_backends.install_for(
        HYPRLAND, _binding(), "/opt/app", runner)

    assert ok is True
    assert "saved" in message
    assert "applied" not in message
    assert not any(call[0] == "hyprctl" for call in runner.calls)
    assert HYPR_MARKER in hypr_conf.read_text(encoding="utf-8")


def test_hyprland_install_without_a_config_returns_the_manual_line(
        monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing"))
    monkeypatch.setattr("shutil.which", _which({}))
    runner = FakeRunner()

    ok, message = shortcut_backends.install_for(
        HYPRLAND, _binding(), "/opt/app", runner)

    assert ok is False
    assert ("bind = CTRL SHIFT, B, exec, /opt/app --toggle "
            f"{HYPR_MARKER}") in message
    assert runner.calls == []


def test_hyprland_reload_falls_back_to_config_only(monkeypatch):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))

    def handler(call):
        if call[-1] == "config-only":
            return CommandResult(True)
        return CommandResult(False, stderr="reload failed")

    runner = FakeRunner(handler=handler)

    assert shortcut_hyprland.reload_config(runner) == ""
    assert runner.calls == [["/usr/bin/hyprctl", "reload"],
                            ["/usr/bin/hyprctl", "reload", "config-only"]]


def test_hyprland_reload_failure_reports_the_last_error(monkeypatch):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))
    runner = FakeRunner(handler=lambda call: CommandResult(
        False, stderr="could not parse config"))

    assert shortcut_hyprland.reload_config(runner) == "could not parse config"
    assert runner.calls == [["/usr/bin/hyprctl", "reload"],
                            ["/usr/bin/hyprctl", "reload", "config-only"]]


def test_hyprland_reload_without_hyprctl_reports_it(monkeypatch):
    monkeypatch.setattr("shutil.which", _which({}))
    runner = FakeRunner()

    assert shortcut_hyprland.reload_config(runner) == "hyprctl is not on PATH"
    assert runner.calls == []


def test_hyprland_reload_failure_saves_but_tells_the_user_to_reload(
        monkeypatch, hypr_conf, caplog):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))
    monkeypatch.setattr(shortcut_hyprland, "reload_config",
                        lambda runner: "reload failed")
    runner = FakeRunner()

    with caplog.at_level("WARNING", logger="mewgenics_overlay.desktopshortcut"):
        ok, message = shortcut_backends.install_for(
            HYPRLAND, _binding(), "/opt/app", runner)

    assert ok is True
    assert "saved" in message
    assert "Reload Hyprland" in message
    assert HYPR_MARKER in hypr_conf.read_text(encoding="utf-8")
    # A live-apply failure is a real warning, not a silent degrade.
    assert any("live reload failed" in r.message for r in caplog.records)


def test_hyprland_backup_is_written_only_when_the_config_changes(
        monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))
    backup = _backup_of(hypr_conf)
    original = hypr_conf.read_text(encoding="utf-8")

    # First install changes the file -> the pre-change content is backed up.
    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())
    assert backup.read_text(encoding="utf-8") == original
    after_first = hypr_conf.read_text(encoding="utf-8")

    # A second install with the same binding changes nothing, so the backup
    # must still hold the original, not the (identical) managed version.
    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())
    assert hypr_conf.read_text(encoding="utf-8") == after_first
    assert backup.read_text(encoding="utf-8") == original

    # A different binding does change the file, so the backup is refreshed.
    shortcut_backends.install_for(HYPRLAND, _binding(True, True, False, "K"),
                                  "/opt/app", FakeRunner())
    assert backup.read_text(encoding="utf-8") == after_first


def test_hyprland_uses_one_stable_backup_name(monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which", _which({}))

    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())
    shortcut_backends.install_for(HYPRLAND, _binding(True, True, False, "K"),
                                  "/opt/app", FakeRunner())

    backups = list(hypr_conf.parent.glob("hyprland.conf*"))
    assert [p.name for p in backups] == [
        hypr_conf.name, hypr_conf.name + _HYPR_BACKUP]


def test_hyprland_write_uses_a_same_directory_temp_and_rename(
        monkeypatch, hypr_conf):
    # The config is the user's live Hyprland file: it must jump from the old
    # content to the new one via a same-directory temp + os.replace, never a
    # truncating in-place write.
    original = hypr_conf.read_text(encoding="utf-8")
    captured = {}
    real_replace = os.replace

    def watching_replace(src, dst):
        if Path(dst) == hypr_conf:
            captured["tmp"] = Path(src)
            captured["tmp_content"] = Path(src).read_text(encoding="utf-8")
            captured["dest_before"] = Path(dst).read_text(encoding="utf-8")
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", watching_replace)

    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())

    assert captured["tmp"].parent == hypr_conf.parent
    # The destination still held the full old content at the moment of the
    # rename: no partial bytes ever reached it.
    assert captured["dest_before"] == original
    assert HYPR_MARKER in captured["tmp_content"]
    assert HYPR_MARKER in hypr_conf.read_text(encoding="utf-8")


def test_hyprland_failed_rename_leaves_the_config_intact(
        monkeypatch, hypr_conf, caplog):
    # A crash (or full disk) during the rename must leave the user's live
    # config exactly as it was, with no half-written file and no temp litter.
    original = hypr_conf.read_text(encoding="utf-8")
    observations = []

    def failing_replace(src, dst):
        if Path(dst) == hypr_conf:
            observations.append(hypr_conf.read_text(encoding="utf-8"))
        raise OSError("simulated rename failure")

    monkeypatch.setattr("os.replace", failing_replace)

    with caplog.at_level("WARNING",
                         logger="mewgenics_overlay.desktopshortcut"):
        ok, message = shortcut_backends.install_for(
            HYPRLAND, _binding(), "/opt/app", FakeRunner())

    assert ok is False
    assert "Could not update" in message
    assert hypr_conf.read_text(encoding="utf-8") == original
    # Every observation at the would-be rename was the full old content, so
    # the destination was never observed partially written.
    assert observations and all(seen == original for seen in observations)
    # The temp file is cleaned up rather than left in the config directory.
    assert list(hypr_conf.parent.glob("*.tmp")) == []


def test_hyprland_remove_strips_only_the_managed_line(monkeypatch, hypr_conf):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))
    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())
    managed = hypr_conf.read_text(encoding="utf-8")

    runner = FakeRunner()
    ok, message = shortcut_backends.remove_for(HYPRLAND, runner)

    assert ok is True
    text = hypr_conf.read_text(encoding="utf-8")
    assert HYPR_MARKER not in text
    assert "bind = SUPER, Q, exec, kitty" in text
    # Removal also reloads; there is no (invalid) ``keyword unbind`` call.
    assert ["/usr/bin/hyprctl", "reload"] in runner.calls
    assert not any(call[1] == "keyword" for call in runner.calls)
    assert _backup_of(hypr_conf).read_text(encoding="utf-8") == managed


def test_hyprland_remove_reports_a_failed_reload(monkeypatch, hypr_conf,
                                                 caplog):
    monkeypatch.setattr("shutil.which",
                        _which({"hyprctl": "/usr/bin/hyprctl"}))
    shortcut_backends.install_for(HYPRLAND, _binding(), "/opt/app",
                                  FakeRunner())
    runner = FakeRunner(handler=lambda call: CommandResult(False,
                                                           stderr="boom"))

    with caplog.at_level("WARNING", logger="mewgenics_overlay.desktopshortcut"):
        ok, message = shortcut_backends.remove_for(HYPRLAND, runner)

    assert ok is True
    assert "removed" in message.lower()
    assert "Reload Hyprland" in message
    assert any("live reload failed" in r.message for r in caplog.records)


def test_hyprland_remove_is_idempotent_when_nothing_is_managed(
        monkeypatch, hypr_conf):
    original = hypr_conf.read_text(encoding="utf-8")
    runner = FakeRunner()
    ok, message = shortcut_backends.remove_for(HYPRLAND, runner)

    assert ok is True
    assert "No managed" in message
    assert hypr_conf.read_text(encoding="utf-8") == original
    assert not _backup_of(hypr_conf).exists()
    assert runner.calls == []


def test_hyprland_remove_without_a_config_reports_nothing_to_do(
        monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing"))
    ok, message = shortcut_backends.remove_for(HYPRLAND, FakeRunner())
    assert ok is True
    assert "No Hyprland config" in message


def test_hyprland_bind_line_is_marked_and_managed():
    binding = _binding(True, False, True, "B")
    line = shortcut_hyprland.hypr_bind_line(binding, "/opt/app")
    assert line == ("bind = CTRL SHIFT, B, exec, /opt/app --toggle "
                    f"{HYPR_MARKER}")
    assert shortcut_hyprland.is_managed_hypr(line) is True
    assert shortcut_hyprland.is_managed_hypr("bind = SUPER, Q, exec, kitty") \
        is False


# ── 6. unknown desktop: manual instructions, no writes ─────────────────────
def test_other_desktop_install_returns_manual_instructions():
    runner = FakeRunner()
    ok, message = shortcut_backends.install_for(
        OTHER, _binding(True, True, False, "K"), "/opt/app", runner)

    assert ok is False
    assert "/opt/app --toggle" in message
    assert "Ctrl+Alt+K" in message
    assert runner.calls == []


def test_other_desktop_remove_returns_manual_instructions():
    # On an unsupported desktop there was never an integration to remove, so
    # this is a successful no-op: ``ok=True`` with the informational text (an
    # error colour in the UI would be wrong for "nothing to do").
    runner = FakeRunner()
    ok, message = shortcut_backends.remove_for(OTHER, runner)
    assert ok is True
    assert "No automatic desktop shortcut integration" in message
    assert "manually" in message.lower()
    assert runner.calls == []


# ── 7. public desktopshortcut API dispatch ─────────────────────────────────
def test_install_dispatches_to_the_detected_desktop(monkeypatch):
    _clear_desktop_env(monkeypatch)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(desktopshortcut, "command_path", lambda: "/opt/app")
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    ok, message = desktopshortcut.install("Ctrl+Alt+K", runner)

    assert ok is True
    assert ["gsettings", "set", _GNOME_SCHEMA, "binding",
            "<Control><Alt>K"] in runner.calls
    assert ["gsettings", "set", _GNOME_SCHEMA, "command",
            "/opt/app --toggle"] in runner.calls


def test_install_with_an_unusable_binding_uses_the_default(monkeypatch):
    _clear_desktop_env(monkeypatch)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(desktopshortcut, "command_path", lambda: "/opt/app")
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    desktopshortcut.install("not a combo at all", runner)

    assert ["gsettings", "set", _GNOME_SCHEMA, "binding",
            "<Control><Shift>B"] in runner.calls


def test_remove_dispatches_to_the_detected_desktop(monkeypatch):
    _clear_desktop_env(monkeypatch)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    runner = FakeRunner(results=[CommandResult(True, stdout="[]")])

    ok, message = desktopshortcut.remove(runner)

    assert ok is True
    assert ["gsettings", "reset", _GNOME_SCHEMA, "name"] in runner.calls


def test_public_api_re_exports_the_shared_primitives():
    # The module advertises these through __all__ so callers/tests share one
    # runner and result type with the backends.
    assert desktopshortcut.CommandResult is shortcut_common.CommandResult
    assert desktopshortcut.run_command is shortcut_common.run_command
