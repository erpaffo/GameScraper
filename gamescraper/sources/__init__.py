from .arcadedb import ArcadeDB
from .igdb import Igdb
from .libretro import Libretro
from .offline import LaunchBox, TgdbOffline
from .rawg import Rawg
from .screenscraper import ScreenScraper
from .thegamesdb import TheGamesDb
from .wikipedia import Wikipedia

SOURCES = {s.name: s for s in (Libretro, LaunchBox, TgdbOffline, ArcadeDB, Igdb, ScreenScraper, Rawg, TheGamesDb, Wikipedia)}
