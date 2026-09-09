# NOTES — things to tackle later & future features

Informal backlog for the overlay. Nothing here is scheduled unless it says so.

## Bugs (small)

- ✅ **Scaling** resolved (v0.1.47+): HiDPI PassThrough rounding policy at
  startup (fractional 125/150% honored) + user zoom: header % button
  (100/150/200/300%), Ctrl++/Ctrl+-/Ctrl+0, Ctrl+wheel; scales app font,
  table columns and header buttons; persisted. Verify on the Windows
  tester's HiDPI display.


## Open question / needs tester

- **Ctrl+Shift+B hotkey on Windows 11** may not fire while Mewgenics is in
  (exclusive) fullscreen — global hotkeys can be swallowed by the game's
  capture. Logging added (overlay.log shows RegisterHotKey failures); the
  tray icon always works as a summon fallback. If the tester still can't
  summon in fullscreen, a low-level keyboard-hook backend is the follow-up.

## Review notes

- Zoom scaling has three paths (theme stylesheet regex, _scale_icon_buttons, scale_columns); new zoomable widgets need their own hookup.
- Search-box interaction state uses two booleans; if a third mode appears, consolidate into a state enum. eventFilter: dispatch by `watched`.

## Future features

- **Auto-update** — check the latest release on startup; show a confirmation
  box before downloading/offering the new binary.
- **Launcher screen** — pick save / recent saves / settings before opening
  the main palette.
- **In-app bug reporting** — send reports straight from the app to the
  Bugbox API, with a unique verification code derived from save-file data +
  app version (+ maybe install id) so reports are attributable without
  accounts.
- **"How does this cat look?"** — if feasible, a button/panel rendering the
  cat's visual appearance from the save's mutation data (needs research on
  how far the save + gpak data can drive a sprite preview).

## Refactor roadmap (in progress)

- Step 1 done: `SaveController` extracted.
- Step 2 done: `PartnerTableWidget` (config/sort/render/tooltips) +
  Defects tri-state.
- Step 3 done: `FocusedCatPanel` extracted (ui/focuspanel.py).
- Step 4 done: coordinator cleanup + mechanical tidy (slots=True on
  donation dataclasses, glyph constants in theme.py).
- Work-order item 2 done: donation reason copy consolidated into
  core/donations.py (keeper note at report time + rating_why()); the
  Donations tab only formats engine output.
- Refactor complete: PaletteWindow is now a thin coordinator over
  SaveController / PartnerTableWidget / FocusedCatPanel.
