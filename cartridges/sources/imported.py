# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2025-2026 kramo

import itertools
import json
import logging
import time
from collections.abc import Generator, Iterable
from gettext import gettext as _
from json import JSONDecodeError
from pathlib import Path

from cartridges import cover
from cartridges.cover import COVERS_DIR
from cartridges.games import GAMES_DIR, Game

ID, NAME = "imported", _("Added")


def get_games() -> Generator[Game]:
    """Manually added games."""
    for path in get_paths():
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, JSONDecodeError, UnicodeDecodeError):
            logging.getLogger(__name__).warning("Added: could not read a game record")
            continue

        if not isinstance(data, dict):
            logging.getLogger(__name__).warning("Added: invalid game record shape")
            continue

        try:
            game = Game.from_data(data)
        except TypeError:
            logging.getLogger(__name__).warning("Added: invalid game record fields")
            continue

        base = COVERS_DIR / game.game_id
        game.cover = cover.at_path(f"{base}.gif") or cover.at_path(f"{base}.tiff")
        yield game


def new(games: Iterable[Game]) -> Game:
    """Create a new game for the user to manually set its properties."""
    ids = {p.stem for p in get_paths()}
    ids.update(game.game_id for game in games)
    number = next(i for i in itertools.count() if f"{ID}_{i}" not in ids)
    return Game(game_id=f"{ID}_{number}", source=ID, added=int(time.time()))


def get_paths() -> Generator[Path]:
    """Get the paths of all imported games on disk."""
    yield from GAMES_DIR.glob("imported_*.json")
