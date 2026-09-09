# NOTES — things to tackle later & future features

Informal backlog for the overlay. Nothing here is scheduled unless it says so.

## Bugs (small)

- **Scaling on large resolutions:** at big monitor resolutions the overlay
  (and its text) renders tiny, even when Mewgenics itself is set to a lower
  resolution. Ideas: a button to scale the window 2×/4×, and/or
  `Ctrl + mouse-wheel` zooming (scale the root layout).


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
