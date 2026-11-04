# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2023 Geoffrey Coulaud
# SPDX-FileCopyrightText: Copyright 2022-2026 kramo

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Generator
from gettext import gettext as _
from hashlib import sha256
from json import JSONDecodeError
from pathlib import Path
from typing import Any, override

from cartridges import cover
from cartridges.games import Game

from . import APPDATA, APPLICATION_SUPPORT, CONFIG, FLATPAK, OPEN

ID, NAME = "heroic", _("Heroic")

_logger = logging.getLogger(__name__)

_CONFIG_PATHS = (
    CONFIG / "heroic",
    FLATPAK / "com.heroicgameslauncher.hgl" / "config" / "heroic",
    APPDATA / "heroic",
    APPLICATION_SUPPORT / "heroic",
)


class _Source(ABC):
    ID: str
    COVER_URI_PARAMS = ""
    LIBRARY_KEY = "library"

    @classmethod
    @abstractmethod
    def library_path(cls) -> Path: ...

    @classmethod
    def installed_app_names(cls) -> set[str] | None:
        return None


class _SideloadSource(_Source):
    ID = "sideload"
    LIBRARY_KEY = "games"

    @classmethod
    def library_path(cls) -> Path:
        return Path("sideload_apps", "library.json")


class _StoreSource(_Source):
    _INSTALLED_PATH: Path

    @classmethod
    def library_path(cls) -> Path:
        return Path("store_cache", f"{cls.ID}_library.json")

    @override
    @classmethod
    def installed_app_names(cls) -> set[str]:
        data = _read_json(_config_dir() / cls._INSTALLED_PATH)
        return set(cls._installed(data))

    @staticmethod
    def _installed(data: Any) -> Generator[str]:  # noqa: ANN401
        if isinstance(data, dict):
            yield from data
        elif data is not None:
            _logger.warning("Heroic: invalid installed-game index")


class _LegendarySource(_StoreSource):
    ID = "legendary"
    COVER_URI_PARAMS = "?h=400&resize=1&w=300"
    _INSTALLED_PATH = Path("legendaryConfig", "legendary", "installed.json")


class _GOGSource(_StoreSource):
    ID = "gog"
    LIBRARY_KEY = "games"
    _INSTALLED_PATH = Path("gog_store", "installed.json")

    @override
    @staticmethod
    def _installed(data: Any) -> Generator[str]:
        if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
            if data is not None:
                _logger.warning("Heroic GOG: invalid installed-game index")
            return
        for entry in data["installed"]:
            if isinstance(entry, dict) and isinstance(entry.get("appName"), str):
                yield entry["appName"]
            else:
                _logger.warning("Heroic GOG: invalid installed-game entry")


class _NileSource(_StoreSource):
    ID = "nile"
    LIBRARY_PATH = Path("store_cache", "nile_library.json")
    _INSTALLED_PATH = Path("nile_config", "nile", "installed.json")

    @override
    @staticmethod
    def _installed(data: Any) -> Generator[str]:
        if not isinstance(data, list):
            if data is not None:
                _logger.warning("Heroic Nile: invalid installed-game index")
            return
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                yield entry["id"]
            else:
                _logger.warning("Heroic Nile: invalid installed-game entry")


def get_games() -> Generator[Game]:
    """Installed Heroic games."""
    for source in _LegendarySource, _GOGSource, _NileSource, _SideloadSource:
        yield from _games_from(source)


def _config_dir() -> Path:
    for path in _CONFIG_PATHS:
        if path.is_dir():
            return path

    raise FileNotFoundError


def _read_json(path: Path) -> Any:  # noqa: ANN401
    try:
        with path.open(encoding="utf-8") as fp:
            data = json.load(fp)
    except FileNotFoundError:
        return None
    except (OSError, JSONDecodeError, UnicodeDecodeError):
        _logger.warning("Heroic: could not read a library or configuration file")
        return None
    if not isinstance(data, dict | list):
        _logger.warning("Heroic: invalid library or configuration shape")
        return None
    return data


def _hidden_app_names() -> Generator[str]:
    config = _read_json(_config_dir() / "store" / "config.json")
    if config is None:
        return
    if not isinstance(config, dict) or not isinstance(config.get("games"), dict):
        _logger.warning("Heroic: invalid hidden-game configuration")
        return
    hidden = config["games"].get("hidden", [])
    if not isinstance(hidden, list):
        _logger.warning("Heroic: invalid hidden-game list")
        return
    for game in hidden:
        if isinstance(game, dict) and isinstance(game.get("appName"), str):
            yield game["appName"]
        else:
            _logger.warning("Heroic: invalid hidden-game entry")


def _games_from(source: type[_Source]) -> Generator[Game]:
    data = _read_json(_config_dir() / source.library_path())
    if data is None:
        return
    if not isinstance(data, dict) or not isinstance(data.get(source.LIBRARY_KEY), list):
        _logger.warning("Heroic %s: invalid library shape", source.ID)
        return
    library = data[source.LIBRARY_KEY]
    source_id = f"{ID}_{source.ID}"
    images_cache = _config_dir() / "images-cache"
    installed = source.installed_app_names()
    hidden = set(_hidden_app_names())

    for entry in library:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) or not entry[field]
            for field in ("app_name", "runner", "title")
        ):
            _logger.warning("Heroic %s: invalid game entry", source.ID)
            continue
        app_name = entry["app_name"]
        if installed is not None and app_name not in installed:
            continue
        art = entry.get("art_square", "")
        developer = entry.get("developer")
        if not isinstance(art, str) or not isinstance(developer, str | None):
            _logger.warning("Heroic %s: invalid optional game metadata", source.ID)
        cover_uri = f"{art if isinstance(art, str) else ''}{source.COVER_URI_PARAMS}"
        cover_path = images_cache / sha256(cover_uri.encode()).hexdigest()
        yield Game(
            executable=f"{OPEN} heroic://launch/{entry['runner']}/{app_name}",
            game_id=f"{source_id}_{app_name}",
            source=source_id,
            hidden=app_name in hidden,
            name=entry["title"],
            developer=developer if isinstance(developer, str) else None,
            cover=cover.at_path(cover_path),
        )
