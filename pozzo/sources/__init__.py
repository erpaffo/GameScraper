from .arcadedb import ArcadeDB
from .igdb import Igdb
from .libretro import Libretro
from .nsp import Nsp
from .offline import LaunchBox, TgdbOffline
from .rawg import Rawg
from .screenscraper import ScreenScraper
from .thegamesdb import TheGamesDb
from .wikipedia import Wikipedia

SOURCES = {s.name: s for s in (Nsp, Libretro, LaunchBox, TgdbOffline, ArcadeDB, Igdb, ScreenScraper, Rawg, TheGamesDb, Wikipedia)}
