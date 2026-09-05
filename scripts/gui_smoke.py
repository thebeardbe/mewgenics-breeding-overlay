#!/usr/bin/env python3
"""Offscreen GUI smoke test.

Runs the real PySide6 widgets without a display (QT_QPA_PLATFORM=offscreen),
drives the palette through its full data flow, and asserts the interesting
bits. Requires PySide6; run inside the `withGui` nix shell or a venv.

Usage:
    QT_QPA_PLATFORM=offscreen python scripts/gui_smoke.py [save.sav]
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

# Isolate settings so the smoke test never touches the real config dir.
_tmp_cfg = tempfile.mkdtemp(prefix="mewgenics-cfg-")
os.environ.setdefault("XDG_CONFIG_HOME", _tmp_cfg)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mewgenics_overlay.ui.palette import PaletteWindow  # noqa: E402


def _wait_until(cond, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    QApplication.processEvents()
    return cond()


def main() -> int:
    save = sys.argv[1] if len(sys.argv) > 1 else None
    app = QApplication.instance() or QApplication(sys.argv[:1])

    pal = PaletteWindow()
    if save:
        pal.open_save(save)

    # 1) session should load and the search box should index cats
    loaded = _wait_until(lambda: pal._session is not None and pal._session.alive)
    assert loaded, "save never loaded"
    n = len(pal._session.alive)
    assert n > 0, "no alive cats parsed"
    print(f"[ok] loaded save: {n} alive cats")

    # 2) pick a cat through the public API, partners must render
    cat = pal._session.alive[0]
    pal.set_focus(cat)
    rows = _wait_until(lambda: pal._table.rowCount() > 0)
    assert rows, "partners table never rendered"
    print(f"[ok] {pal._table.rowCount()} partner rows rendered for {cat.name}")

    # 3) column sorting: risk asc, risk desc, back to default, then name asc
    def _risk_column() -> list[float]:
        vals = []
        for r in range(pal._table.rowCount()):
            it = pal._table.item(r, 4)       # Risk column
            txt = it.text() if it else "—"
            if txt != "—":
                vals.append(float(txt.rstrip("%")))
        return vals

    # every row carries a Family label and a numeric generation gap
    for r in range(pal._table.rowCount()):
        fam = pal._table.item(r, 1)
        gap = pal._table.item(r, 2)
        assert fam is not None and fam.text(), f"row {r} missing Family"
        txt = gap.text()
        if txt[:1] in "+-":
            txt = txt[1:]
        assert txt.isdigit(), f"row {r} GenΔ not numeric: {gap.text()!r}"
    print("[ok] Family + GenΔ columns populated")

    # header tooltips exist and cell tooltips don't refer back to them
    hdr_tips = [pal._table.horizontalHeaderItem(i)
                for i in range(pal._table.columnCount())]
    assert all(it is not None and it.toolTip() for it in hdr_tips), \
        "every column header must carry an explanation tooltip"
    for r in range(pal._table.rowCount()):
        for c in range(pal._table.columnCount()):
            it = pal._table.item(r, c)
            assert it is None or "column header" not in (it.toolTip() or "")
    print("[ok] header tooltips present; cell tooltips are header-independent")

    risks = _risk_column()
    if len(risks) >= 2:
        pal._on_header_clicked(4)                     # risk asc
        asc = _risk_column()
        assert asc == sorted(asc), "risk column not ascending"
        pal._on_header_clicked(4)                     # risk desc
        desc = _risk_column()
        assert desc == sorted(desc, reverse=True), "risk column not descending"
        pal._on_header_clicked(4)                     # back to default
        assert pal._sort_col is None, "third header click should reset order"
        pal._on_header_clicked(1)                     # family label asc
        fams = [pal._table.item(r, 1).text().lower()
                for r in range(pal._table.rowCount())
                if not pal._table.item(r, 0).text().endswith("(✗)")]
        assert fams == sorted(fams), "family column not ascending"
        pal._on_header_clicked(1)
        pal._on_header_clicked(1)                     # back to default
        pal._on_header_clicked(0)                     # name asc
        names = [pal._table.item(r, 0).text().lower()
                 for r in range(pal._table.rowCount())
                 if not pal._table.item(r, 0).text().endswith("(✗)")]
        assert names == sorted(names), "name column not ascending"
        pal._on_header_clicked(0)
        pal._on_header_clicked(0)                     # back to default
        print("[ok] column sorting (risk asc/desc, family, name, reset)")
    else:
        print("[skip] sorting check (fewer than 2 compatible partners)")

    # 4) swap focus to the top partner via double-click semantics
    item = pal._table.item(0, 0)
    assert item is not None
    row, _ = item.data(0x0100)  # Qt.ItemDataRole.UserRole
    pal.set_focus(row.partner)
    ok2 = _wait_until(lambda: pal._table.rowCount() > 0)
    assert ok2
    print(f"[ok] re-rooted to {row.partner.name}")

    # 4) simulated live save rewrite -> auto reload must adopt without crash
    pal._on_save_changed()          # same path as the watcher callback
    reloaded = _wait_until(lambda: pal._session is not None
                           and len(pal._session.alive) > 0, timeout=8)
    assert reloaded
    rerendered = _wait_until(lambda: pal._table.rowCount() > 0)
    assert rerendered
    print("[ok] save rewrite reloaded and partners re-rendered")

    # 5) search box filters and results are populated
    pal._search.setText(cat.name[:3])
    got_results = _wait_until(lambda: pal._results.count() > 0)
    assert got_results, "search results never populated"
    print(f"[ok] search returns {pal._results.count()} result(s)")

    pal.shutdown()
    print("GUI SMOKE PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # print clean failure for the harness
        print(f"GUI SMOKE FAILED: {exc!r}")
        raise
