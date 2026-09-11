# NOTES — things to tackle later & future features

Informal backlog for the overlay. Nothing here is scheduled unless it says so.

## v0.3.0 plan (agreed, not started)

Waiting on the tester's results for v0.2.1 (Windows hotkey) and v0.2.2
(GNOME/KDE/Hyprland desktop shortcut). Picked up after that report.

1. **XDG portal `GlobalShortcuts` backend (primary Linux path).**
   One API (`org.freedesktop.portal.GlobalShortcuts`) instead of per-DE
   config edits: the desktop shows its own "assign a shortcut" dialog and
   owns the key, so it works on Wayland. Confirmed present on the dev
   machine (xdg-desktop-portal with hyprland/gnome/gtk backends), which
   makes it the only clean path for Hyprland-Lua setups too.
   Support: KDE Plasma 5.27+, GNOME 48+, Hyprland via its portal.
   Backends order becomes: portal → GNOME gsettings / KDE KGlobalAccel /
   Hyprland config → manual instructions. Implement with QtDBus (already a
   PySide6 dependency) and keep the existing per-DE scripts as fallback.
2. **Hyprland Lua config support.** The dev machine runs `hyprland.lua`
   (`host.lua`, `hyprland.lua` in `~/.config/hypr`), not `hyprland.conf`, so
   `--setup-shortcut` correctly refuses to invent a config file and prints
   the manual line. Detect the launch config (`hyprctl` reports it, or the
   Lua file's existence) and offer the Lua equivalent
   (`hl.bind(...)`), or rely on the portal backend from item 1.
3. **X11 `XGrabKey` backend** for plain X11 sessions (GNOME Xorg, KDE X11):
   a real in-app global grab via ctypes `libX11`, no dconf/KDE config edits.
   Skipped automatically under Wayland. This is the only way to match the
   Windows experience on X11.
4. **Windows fallback: low-level keyboard hook.** Only if the v0.2.1
   `RegisterHotKey` fix still fails while Mewgenics is in exclusive
   fullscreen (some games swallow global hotkeys). A `WH_KEYBOARD_LL` hook
   is the follow-up; the tray icon and the focused-window shortcut remain
   fallbacks.
5. **partnertable.py split (627 lines, the last file past the soft budget).**
   Candidates: column definitions/tooltips, sort state machine, cell
   tooltip builders, malady-line rendering. Palette is already a coordinator
   (544 lines, delegations only).
6. **Fight-room fighter generator** (new feature): score cats for the fight
   rooms using aggression (currently displayed but neutral), stats, and
   abilities; recommend room assignments.

## Bugs (small)

- ✅ **Scaling** resolved (v0.1.47+): HiDPI PassThrough rounding policy at
  startup (fractional 125/150% honored) + user zoom: header % button
  (100/150/200/300%), Ctrl++/Ctrl+-/Ctrl+0, Ctrl+wheel; scales app font,
  table columns and header buttons; persisted. Verify on the Windows
  tester's HiDPI display.

## Open question / needs tester

- ✅ **Ctrl+Shift+B did nothing on Windows** — root cause found (v0.2.1):
  `RegisterHotKey(hwnd=NULL)` delivers `WM_HOTKEY` as `windows_dispatcher_MSG`
  and the filter only accepted `windows_generic_MSG`, so it registered and
  then silently never fired. Fixed; the combo is configurable in
  Settings → Global hotkey with no fallback list. Test on Windows 11 with
  the game focused, including exclusive fullscreen; if it still fails,
  item 4 above (low-level hook) is the next step.
- Pending tester reports: Windows v0.2.1, GNOME v0.2.2, Hyprland v0.2.2.
  Live-verified already on the dev Hyprland machine: single instance stays
  at one process, `--toggle` shows/hides, `--setup-shortcut` prints the
  manual line instead of creating a bogus `hyprland.conf`.

## Review notes

- Zoom scaling has three paths (theme stylesheet regex, TopBar.scale_buttons,
  PartnerTableWidget.scale_columns); new zoomable widgets need their own
  hookup.
- Search-box interaction state uses two booleans; if a third mode appears,
  consolidate into a state enum. eventFilter: dispatch by `watched`.
- Hotkey platform code is split across `hotkeybinding` (pure model),
  `hotkey` (Windows grab), `hotkeyctl` (controller), `desktopshortcut` /
  `shortcut_*` (Linux backends) and `singleton` (`--toggle`). Keep the
  combo syntax converters single-sourced on `HotkeyBinding`.

## Future features

- **In-game bridge (in progress)** — the separate `mewgenics-breeding-mod` DLL
  talks to the overlay over loopback TCP (protocol v1, `{"type":"focus","key":N}`).
  `core/bridge.py` owns the protocol and resolution, `ui/bridgectl.py` hops onto
  the UI thread, and `app.py` focuses the cat and shows the window. Default port
  `45780` (`bridge_port`), disable with `bridge_enabled`. The mod currently only
  logs cats at save load; the in-game button and the overlay-to-game select path
  are still to come.
- **Auto-update** — check the latest release on startup; show a confirmation
  box before downloading/offering the new binary. (The check + website
  download button shipped in v0.2.0; actual self-update is not done.)
- **Launcher screen** — pick save / recent saves / settings before opening
  the main palette.
- **In-app bug reporting** — send reports straight from the app to the
  Bugbox API, with a unique verification code derived from save-file data +
  app version (+ maybe install id) so reports are attributable without
  accounts.
- **"How does this cat look?"** — if feasible, a button/panel rendering the
  cat's visual appearance from the save's mutation data (needs research on
  how far the save + gpak data can drive a sprite preview).

## Refactor roadmap (complete)

- ✅ PaletteWindow god-file split finished (v0.1.52, continued in v0.2.1/2):
  `palette.py` went 1463 → 544 lines and is now a coordinator (lifecycle,
  tray/hotkey/open-save entry points, thin delegations). Modules:
  layout, tablectl, assets, chrome, searchbox, partnertable, focuspanel,
  bestmatch, partneractions, roombar, savepanel, windowstate, zoom,
  themectl, reloader, updatenotice, aboutdialog, pinning, links,
  hotkeybinding, hotkey, hotkeyctl, singleton, desktopshortcut,
  shortcut_common, shortcut_backends, shortcut_hyprland, shortcutworker.
- Remaining size item is `partnertable.py` (627 lines) — see v0.3.0 item 5.
