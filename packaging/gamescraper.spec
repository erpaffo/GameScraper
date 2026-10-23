# PyInstaller: due eseguibili nella stessa cartella (stesse librerie, nessun doppione).
#   GameScraper        -> l'app con finestra (doppio clic)
#   gamescraper-cli    -> i comandi da terminale (scrape, export, library, ...)
# Su macOS in piu' viene creato GameScraper.app.
#
# Uso:  pyinstaller packaging/gamescraper.spec --noconfirm      (dalla cartella del progetto)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH e' definito da PyInstaller
sys.path.insert(0, str(ROOT))
from gamescraper import __version__  # noqa: E402

APP = ROOT / "gamescraper" / "app"
datas = [
    (str(APP / "static"), "gamescraper/app/static"),
    (str(APP / "themes"), "gamescraper/app/themes"),
    (str(APP / "plugins"), "gamescraper/app/plugins"),
    (str(ROOT / "config.example.toml"), "."),
    (str(ROOT / "gamescraper" / "remote" / "vendor"), "gamescraper/remote/vendor"),  # ViGEmClient.dll
]
datas += collect_data_files("fake_useragent")  # usata da howlongtobeatpy
hidden = (collect_submodules("uvicorn") + collect_submodules("gamescraper") + collect_submodules("rich._unicode_data")
          + collect_submodules("qrcode") + ["h2", "hpack", "hyperframe", "multipart", "PIL.WebPImagePlugin"])
excludes = ["tkinter", "matplotlib", "numpy", "pytest", "playwright", "IPython"]

icon_ico = str(ROOT / "packaging" / "icon.ico")
icon_png = str(ROOT / "packaging" / "icon.png")
icon = icon_ico if sys.platform == "win32" else icon_png


def analysis(script):
    return Analysis([str(ROOT / "packaging" / script)], pathex=[str(ROOT)], datas=datas,  # noqa: F821
                    hiddenimports=hidden, excludes=excludes, noarchive=False)


a_app = analysis("entry_app.py")
a_cli = analysis("entry_cli.py")

exe_app = EXE(PYZ(a_app.pure), a_app.scripts, [], exclude_binaries=True, name="GameScraper",  # noqa: F821
              console=False, icon=icon, upx=False)
exe_cli = EXE(PYZ(a_cli.pure), a_cli.scripts, [], exclude_binaries=True, name="gamescraper-cli",  # noqa: F821
              console=True, icon=icon, upx=False)

coll = COLLECT(exe_app, a_app.binaries, a_app.datas, exe_cli, a_cli.binaries, a_cli.datas,  # noqa: F821
               name="GameScraper", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name="GameScraper.app", icon=icon_png, bundle_identifier="io.github.gamescraper",  # noqa: F821
                 version=__version__, info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0",
                                                   "CFBundleDisplayName": "GameScraper"})
