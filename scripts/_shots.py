"""Regenerate the website screenshots (1180x760) from the live overlay:
breeding-dark/-light with L'Via focused, donations-dark with Dr. Beanies."""

import os
import struct
import time

os.environ.setdefault("XDG_CONFIG_HOME", "/tmp/mg-shots")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

app = QApplication([])

from mewgenics_overlay.ui.palette import PaletteWindow

SAV = os.path.expanduser(
    "~/.steam/steam/steamapps/compatdata/686060/pfx/drive_c/users/steamuser/"
    "AppData/Roaming/Glaiel Games/Mewgenics/76561198863371232/saves/"
    "steamcampaign02.sav")
OUT = "/mnt/black-glass/Git-projects/mewgenics-bugbox/static/screenshots"
W, H = 1180, 760


def wait(cond, secs=30):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
        if cond():
            return True
    return False


def grab(path):
    pm = p.grab()
    if pm.width() != W or pm.height() != H:
        pm = pm.scaled(W, H)
    pm.save(path, "PNG")
    with open(path, "rb") as fh:
        w, h = struct.unpack(">II", fh.read(24)[16:24])
    print(path, w, "x", h)


p = PaletteWindow()
p.resize(W, H)
p.show()
p.apply_theme("noir")
p.open_save(SAV)
assert wait(lambda: p._session is not None and p._session.alive), "save"
lvia = next((c for c in p._session.alive if c.name == "L'Via"), None)
assert lvia is not None, "L'Via not found"
p.set_focus(lvia)
wait(lambda: p._table.rowCount() > 0, 40)
time.sleep(1.2)
for _ in range(30):
    app.processEvents()
    time.sleep(0.02)
p._tabs.setCurrentIndex(0)
grab(os.path.join(OUT, "breeding-dark.png"))

p.apply_theme("film")
for _ in range(30):
    app.processEvents()
    time.sleep(0.02)
grab(os.path.join(OUT, "breeding-light.png"))

# back to dark, donations tab, Dr. Beanies
p.apply_theme("noir")
p._tabs.setCurrentIndex(1)
for _ in range(30):
    app.processEvents()
    time.sleep(0.02)
combo = p._donations_tab._combo
idx = next((i for i in range(combo.count())
            if str(combo.itemText(i)).startswith("Dr. Beanies")), 0)
combo.setCurrentIndex(idx)
p._donations_tab._on_npc_selected(idx)
time.sleep(1.0)
for _ in range(40):
    app.processEvents()
    time.sleep(0.02)
grab(os.path.join(OUT, "donations-dark.png"))
p.shutdown()
print("done")
