# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2023 Geoffrey Coulaud
# SPDX-FileCopyrightText: Copyright 2026 kramo

import asyncio
import json
import logging
from collections.abc import Generator
from gettext import gettext as _
from json import JSONDecodeError
from pathlib import Path
from typing import cast

from gi.repository import Gio

from cartridges import cover
from cartridges.games import Game

from . import CONFIG

ID, NAME = "legendary", _("Legendary")

_logger = logging.getLogger(__name__)

_CONFIG_PATH = CONFIG / "legendary"


def get_games() -> Generator[Game]:
    """Installed Legendary games."""
    config = _config_dir()

    try:
        with (config / "installed.json").open() as fp:
            installed = json.load(fp)
    except (OSError, JSONDecodeError, UnicodeDecodeError):
        _logger.warning("Legendary: could not read installed games")
        return

    if not isinstance(installed, dict):
        _logger.warning("Legendary: invalid installed-game index")
        return

    app = cast(Gio.Application, Gio.Application.get_default())
    for entry in installed.values():
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) or not entry[field]
            for field in ("app_name", "title")
        ):
            _logger.warning("Legendary: invalid game entry")
            continue
        if entry.get("is_dlc"):
            continue

        app_name = entry["app_name"]
        title = entry["title"]

        try:
            with (config / "metadata" / f"{app_name}.json").open() as fp:
                data = json.load(fp)
            metadata = data.get("metadata") if isinstance(data, dict) else None
            if not isinstance(metadata, dict):
                _logger.warning("Legendary: invalid optional metadata")
                metadata = {}
        except FileNotFoundError:
            metadata = {}
        except (OSError, JSONDecodeError, UnicodeDecodeError):
            _logger.warning("Legendary: could not read optional metadata")
            metadata = {}
        developer = metadata.get("developer")
        if not isinstance(developer, str | None):
            _logger.warning("Legendary: invalid developer metadata")
            developer = None

        game = Game(
            executable=f"legendary launch {app_name}",
            game_id=f"{ID}_{app_name}",
            source=ID,
            name=title,
            developer=developer,
        )

        images = metadata.get("keyImages", [])
        if not isinstance(images, list):
            _logger.warning("Legendary: invalid artwork metadata")
            images = []
        for image in images:
            if not isinstance(image, dict):
                _logger.warning("Legendary: invalid artwork entry")
                continue
            url = image.get("url")
            if (
                image.get("type") == "DieselGameBoxTall"
                and isinstance(url, str)
                and url
            ):
                app.create_asyncio_task(_update_cover(game, url))
                break

        yield game


def _config_dir() -> Path:
    if _CONFIG_PATH.is_dir():
        return _CONFIG_PATH

    raise FileNotFoundError


async def _update_cover(game: Game, url: str):
    game.cover = await asyncio.to_thread(cover.at_url, url)
