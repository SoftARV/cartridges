# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: Copyright 2025 Zoey Ahmed
# SPDX-FileCopyrightText: Copyright 2025-2026 kramo
# SPDX-FileCopyrightText: Copyright 2025 Jamie Gravendeel

import json
import os
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from shlex import join, quote
from types import UnionType
from typing import Any, NamedTuple, Self

from gi.repository import Gdk, Gio, GObject

from . import DATA_DIR

GAMES_DIR = DATA_DIR / "games"


class _GameProp(NamedTuple):
    name: str
    type_: type | UnionType
    required: bool = False
    editable: bool = False


PROPERTIES: tuple[_GameProp, ...] = (
    _GameProp("added", int),
    _GameProp("executable", str | list, required=True, editable=True),
    _GameProp("game_id", str, required=True),
    _GameProp("source", str, required=True),
    _GameProp("hidden", bool),
    _GameProp("last_played", int),
    _GameProp("name", str, required=True, editable=True),
    _GameProp("developer", str, editable=True),
    _GameProp("removed", bool),
    _GameProp("blacklisted", bool),
    _GameProp("version", float),
)

_SPEC_VERSION = 2.0


class Game(Gio.SimpleActionGroup):
    """Game data class."""

    __gtype_name__ = __qualname__

    added = GObject.Property(type=int)
    executable = GObject.Property(type=str)
    game_id = GObject.Property(type=str)
    source = GObject.Property(type=str)
    hidden = GObject.Property(type=bool, default=False)
    last_played = GObject.Property(type=int)
    name = GObject.Property(type=str)
    developer = GObject.Property(type=str)
    removed = GObject.Property(type=bool, default=False)
    blacklisted = GObject.Property(type=bool, default=False)
    version = GObject.Property(type=float, default=_SPEC_VERSION)

    cover = GObject.Property(type=Gdk.Paintable)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Self:
        """Create a game from data. Useful for loading from JSON."""
        game = cls()

        for prop in PROPERTIES:
            value = data.get(prop.name)

            if not prop.required and value is None:
                continue

            if not isinstance(value, prop.type_):
                raise TypeError

            match prop.name:
                case "executable" if isinstance(value, list):
                    if (
                        not value
                        or not value[0]
                        or any(not isinstance(arg, str) or "\0" in arg for arg in value)
                    ):
                        raise TypeError
                    value = join(value)
                case "version" if value and value > _SPEC_VERSION:
                    raise TypeError
                case "version":
                    continue

            setattr(game, prop.name, value)

        return game

    def play(self):
        """Run the executable command in a shell."""
        subprocess.Popen(  # noqa: S602
            format_executable(self.executable),
            cwd=Path.home(),
            shell=True,
            start_new_session=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )

    def save_changes(self, **changes: Any):
        """Save a proposed record before changing the live object."""
        data = {prop.name: getattr(self, prop.name) for prop in PROPERTIES}
        proposed = self.from_data(data | changes)
        proposed.save()
        for name in changes:
            setattr(self, name, getattr(proposed, name))

    def save(self):
        """Save the game's properties to disk."""
        game_id = self.game_id
        if (
            not game_id
            or game_id in {".", ".."}
            or any(char in game_id for char in ("/", "\\", "\0"))
            or PureWindowsPath(game_id).drive
        ):
            msg = "Game ID must be a non-empty filename without path components"
            raise ValueError(msg)

        path = GAMES_DIR / (game_id + ".json")
        if path.is_symlink():
            msg = "Game record destination must not be a symlink"
            raise OSError(msg)

        properties = {prop.name: getattr(self, prop.name) for prop in PROPERTIES}
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=GAMES_DIR,
                prefix=".game-",
                suffix=".tmp",
                delete=False,
            ) as f:
                temporary_path = Path(f.name)
                json.dump(properties, f, indent=4, sort_keys=True)
            temporary_path.replace(path)
        except BaseException:
            # Cleanup must not hide the original save failure.
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            raise


def format_executable(executable: str) -> str:
    """Get the correct executable for the user's environment."""
    return (
        f"flatpak-spawn --host /bin/sh -c {quote(executable)}"
        if Path("/.flatpak-info").exists()
        else executable
    )
