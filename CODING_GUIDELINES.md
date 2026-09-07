# Coding Guidelines

Basis: **PEP 8** + **Google Python Style Guide** where they overlap, Qt/PySide6
best practice for UI & threading, and this project's specific invariants.
Rules are numbered so the audit in §8 can reference them.

## 0. Project invariants (non-negotiable)

1. **Never modify game data.** Saves open read-only (`file:…?mode=ro`) or are
   copied before parsing. No writes to the `Glaiel Games` folder, ever.
2. **Never touch `ui/…/vendor/`** beyond the documented import rewrites.
   It is upstream MBM code and must stay re-vendorable.
3. **Version lives in exactly three files and is bumped ATOMICALLY — never
   skip one, no exceptions:** `pyproject.toml`, `default.nix`,
   `src/mewgenics_overlay/__init__.py`. A release tag `vX.Y.Z` must equal the
   version string in all three; the release workflow's `version-check` job
   (`.github/workflows/release.yml`) fails any tagged push whose three files
   disagree with each other or with the tag. If you notice a partial bump in
   someone else's commit, fix the missing file in the same PR — partial
   bumps never ship.
4. Core genetics/breeding/UI-facing logic we wrote may import `vendor`, but
   the opposite must never happen.

## 1. Style

- PEP 8: 4-space indent, snake_case names, `UPPER_SNAKE` for constants.
- Prefer **single quotes** for short strings; double quotes when the string
  contains single quotes (or use an escape). Keep to one style per file.
- Line length target **≤ 100**, hard ceiling **120**; wrap long signatures,
  returns and f-strings.
- Imports: stdlib, then third-party, then this package; no `*` imports;
  import modules (`theme as _theme`) when you need live attribute access.
- No unused imports/variables (run the audit below). Name throwaway variables
  `_`, `_x`, or descriptive-but-prefixed.
- Docstrings: module docstring for every module; docstring for every class
  and every public function/method explaining *what* and *why* (not how).

## 2. No magic numbers or scattered literals

- Every semantic number becomes a named constant at module top with a comment
  (see `donations.py` weights, `recommend.py` weights, room/age thresholds).
- **One source of truth per concept**: UI colours only in `theme.py`
  (`C_*`, read via the `theme` module so theme switches apply live);
  NPC definitions in `donations.NPC_PROFILES`; column indexes in the
  `COL_*` constants; version in the three files above.
- No hex colours outside `theme.py` (or an explicitly themed asset).
- Avoid duplicating formulas: import shared constants (e.g. recommender
  weights are reused by the donation matrix).

## 3. Pure logic vs. UI

- Keep genetics/breeding/parsing logic in `core/*` **Qt-free** and testable
  without PySide6. UI code stays in `ui/*`.
- Functions that need game-asset text (effect descriptions) accept an
  optional callable (e.g. `effect_of_cat`) instead of importing assets
  directly; `None` degrades gracefully.
- Prefer returning structured data (dataclasses) over dicts for things the UI
  renders.

## 4. Qt/UI & threading

- **UI objects only on the UI thread.** Heavy work (parsing, scoring,
  gpak) happens in background threads; hand results back through a token-
  guarded queue that a UI `QTimer` drains.
- Don't call `setWindowFlags` at runtime (it re-creates the native window on
  Windows and can make the overlay vanish). Use native equivalents
  (`SetWindowPos`) or OS rules (Hyprland).
- Theme switches must restyle **both** the app and the palette (the palette's
  own stylesheet shadows app-wide changes) and then re-render coloured rows.
- When rebuilding a widget that owns selection (dropdown), capture state
  *before* the rebuild, restore *after*.
- Tooltips: wrap long text (`theme.wrap_tooltip`); use `QDialog`+`QLabel`
  for rich-text dialogs (QMessageBox lacks `setOpenExternalLinks` in PySide6).
- Colour reads: `_theme.C_GOOD` etc. at render time — never cache hex values.
- Native-event filters on Windows must decode via
  `ctypes.wintypes.MSG.from_address(int(message))`.

## 5. Data & parsing

- Prefer `getattr(x, field, default)` for optional parser fields so unit-test
  stubs don't need every attribute.
- Read-only helpers for auxiliary save data (`current_day`,
  `npc_progress_flags`) are best-effort and return safe defaults on error.
- Keep kinship/malady/etc. math next to its game-rule comment (cite the wiki).

## 6. Tests

- Every non-trivial core rule gets a unit test (test file per domain).
- Save-dependent tests read `MEWGENICS_SAMPLE_SAV` and `skip` when unset.
- Run the full suite + `scripts/gui_smoke.py` before releasing.

## 7. Releases

- Bump the version (rule 0.3) in **all three files in one commit** — never a
  partial bump. If AGENTS.md §8 or the README quotes a release number, that
  goes in the same commit too.
- Run the audit checklist (§8) → commit → tag `vX.Y.Z` → push the tag. CI
  (release.yml) verifies the three files + tag agree (`version-check`),
  builds the binaries, and publishes the release. A tag whose versions
  disagree fails CI — that is deliberate, not a nuisance.
- Keep commits atomic and descriptive.

## 8. Audit checklist (run after any large change)

- [ ] `pyflakes src/mewgenics_overlay` (exclude `vendor/`) → no undefined
      names or unused imports
- [ ] No line exceeds 120 in `src/mewgenics_overlay` (exclude `vendor/`)
- [ ] No hex colour literals outside `theme.py`
- [ ] Version identical in the three files and equal to the release tag
      (CI's `version-check` enforces this on `v*` pushes — no skips)
- [ ] AGENTS.md §8 status / README do not quote an older release number
- [ ] `python -m pytest tests/ -q` green
- [ ] `QT_QPA_PLATFORM=offscreen python scripts/gui_smoke.py` green
