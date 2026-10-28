"""Database scaricati in blocco (ricerca locale): LaunchBox e TheGamesDB."""

from .launchbox import LaunchBoxDB
from .tgdb import TgdbDB

BULK = {c.id: c for c in (LaunchBoxDB, TgdbDB)}
