# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2023 Geoffrey Coulaud
# SPDX-FileCopyrightText: Copyright 2022-2026 kramo

import asyncio
import logging
import sqlite3
from collections.abc import Generator
from contextlib import closing
from gettext import gettext as _
from pathlib import Path
from typing import cast

from gi.repository import Gio

from cartridges import cover
from cartridges.games import Game

from . import APPDATA, APPLICATION_SUPPORT, CONFIG, FLATPAK, OPEN

ID, NAME = "itch", _("itch")

_CONFIG_PATHS = (
    CONFIG / "itch",
    FLATPAK / "io.itch.itch" / "config" / "itch",
    APPDATA / "itch",
    APPLICATION_SUPPORT / "itch",
)

_QUERY = """
    SELECT
        games.id,
        games.title,
        games.cover_url,
        games.still_cover_url,
        caves.id
    FROM caves
    INNER JOIN games
    ON caves.game_id = games.id;"""


def get_games() -> Generator[Game]:
    """Installed itch games."""
    app = cast(Gio.Application, Gio.Application.get_default())
    uri = (_config_dir() / "db" / "butler.db").resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            for row in conn.execute(_QUERY):
                if (
                    not isinstance(row[0], int)
                    or not isinstance(row[4], int)
                    or not isinstance(row[1], str)
                    or not row[1]
                ):
                    logging.getLogger(__name__).warning("itch: invalid game row")
                    continue
                game = Game(
                    executable=f"{OPEN} itch://caves/{row[4]}/launch",
                    game_id=f"{ID}_{row[0]}",
                    source=ID,
                    name=row[1],
                )
                url = row[3] or row[2]
                if isinstance(url, str) and url:
                    app.create_asyncio_task(_update_cover(game, url))
                yield game
    except sqlite3.Error as error:
        logging.getLogger(__name__).warning(
            "itch: could not read game database (%s)", type(error).__name__
        )


def _config_dir() -> Path:
    for path in _CONFIG_PATHS:
        if path.is_dir():
            return path

    raise FileNotFoundError


async def _update_cover(game: Game, url: str):
    game.cover = await asyncio.to_thread(cover.at_url, url)
